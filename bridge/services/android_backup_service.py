"""Backs up and restores iiSU's own data inside the Android VM, so a
reinstall (or the Uninstall page) doesn't lose the artwork, collections and
settings iiSU keeps there. The existing Backup & Restore page only covers
this project's config files.

What's covered, using nothing but adb (no root, no emulator-specific
tricks, so it works the same on the AVD and on Waydroid):

- iiSU's media folder  /storage/emulated/0/Android/media/com.iisulauncher
- iiSU's app-specific folder  /storage/emulated/0/Android/data/com.iisulauncher
  (often not readable on newer Android; skipped with a note when it isn't)

Deliberately left out: the MediaBridge inbox (transient hand-off files) and
the placeholder ROM tree under /sdcard/Roms, which every start regenerates
from your real library.

The archive is a plain zip: android/<label>/<path inside that folder>, plus
a manifest. Restore only ever writes back to the folders listed in SOURCES,
whatever a hand-edited archive claims.

The adb-free parts (archive layout, member validation, planning) are pure
and unit-tested; the pull/push glue needs a running VM.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from services.android_storage_service import adb_command, adb_device_ready, android_remote_quote

IISU_PACKAGE = "com.iisulauncher"
SOURCES: dict[str, str] = {
    "media": f"/storage/emulated/0/Android/media/{IISU_PACKAGE}",
    "data": f"/storage/emulated/0/Android/data/{IISU_PACKAGE}",
}
# Paths (relative to a source folder) that are never backed up.
EXCLUDED_PREFIXES = ("iiSULauncher/mediabridge/inbox",)
ARCHIVE_ROOT = "android"
MANIFEST_NAME = "backup_manifest.txt"
PULL_TIMEOUT = 3600


class AndroidBackupError(Exception):
    pass


@dataclass
class BackupResult:
    backed_up: dict[str, int] = field(default_factory=dict)  # label -> file count
    skipped: dict[str, str] = field(default_factory=dict)  # label -> why

    @property
    def total_files(self) -> int:
        return sum(self.backed_up.values())

    def summary(self) -> str:
        parts = [f"{label}: {count} file(s)" for label, count in self.backed_up.items()]
        parts += [f"{label}: skipped ({why})" for label, why in self.skipped.items()]
        return "; ".join(parts) or "nothing to back up"


def default_backup_filename() -> str:
    return "iisu-data-backup-" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".zip"


# == Pure archive helpers ==

def is_excluded(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").strip("/")
    return any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in EXCLUDED_PREFIXES)


def archive_name(label: str, relative_path: str) -> str:
    return f"{ARCHIVE_ROOT}/{label}/{relative_path.replace(chr(92), '/').strip('/')}"


def parse_member(name: str) -> tuple[str, str] | None:
    """(label, relative path) for a safe member of a backup archive, else
    None: only files under a known label, never absolute, never with a
    parent-directory segment, never the excluded inbox."""
    normalized = name.replace("\\", "/")
    if normalized.endswith("/") or normalized.startswith("/"):
        return None
    parts = PurePosixPath(normalized).parts
    if len(parts) < 3 or parts[0] != ARCHIVE_ROOT or parts[1] not in SOURCES:
        return None
    if any(part in ("..", "", ".") or ":" in part for part in parts):
        return None
    relative = "/".join(parts[2:])
    if is_excluded(relative):
        return None
    return parts[1], relative


def write_archive(destination: Path, trees: dict[str, Path], result: BackupResult) -> None:
    """Zips each local tree (already pulled from the VM) under its label."""
    manifest = [
        "Community-iiSU-PC iiSU data backup",
        "Created: " + datetime.now().isoformat(timespec="seconds"),
        "",
    ]
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for label, root in trees.items():
            count = 0
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                if is_excluded(relative):
                    continue
                zf.write(path, archive_name(label, relative))
                count += 1
            result.backed_up[label] = count
            manifest.append(f"{label}: {count} file(s) from {SOURCES[label]}")
        for label, why in result.skipped.items():
            manifest.append(f"{label}: skipped ({why})")
        zf.writestr(MANIFEST_NAME, "\n".join(manifest) + "\n")


def inspect_archive(source: Path) -> dict[str, list[str]]:
    """{label: [relative paths]} of everything restorable in the archive.
    Raises AndroidBackupError if it isn't one of ours or has nothing safe."""
    try:
        with zipfile.ZipFile(source) as zf:
            found: dict[str, list[str]] = {}
            for name in zf.namelist():
                parsed = parse_member(name)
                if parsed:
                    found.setdefault(parsed[0], []).append(parsed[1])
    except zipfile.BadZipFile as exc:
        raise AndroidBackupError("That file is not a valid ZIP backup.") from exc
    if not found:
        raise AndroidBackupError("This archive doesn't contain any iiSU data from a Community-iiSU-PC backup.")
    return found


def extract_archive(source: Path, dest_root: Path) -> dict[str, Path]:
    """Extracts only the safe members to dest_root/<label>/... and returns
    {label: folder}."""
    folders: dict[str, Path] = {}
    with zipfile.ZipFile(source) as zf:
        for name in zf.namelist():
            parsed = parse_member(name)
            if not parsed:
                continue
            label, relative = parsed
            target = dest_root / label / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            folders[label] = dest_root / label
    return folders


# == adb glue ==

def _remote_exists(remote: str) -> bool:
    result = adb_command("shell", f"[ -d {android_remote_quote(remote)} ] && echo yes", timeout=30)
    return result.returncode == 0 and "yes" in result.stdout


def create_backup(destination: Path) -> BackupResult:
    ready, detail = adb_device_ready()
    if not ready:
        raise AndroidBackupError(detail or "The Android VM isn't running. Open Community-iiSU-PC first.")

    result = BackupResult()
    work = Path(tempfile.mkdtemp(prefix="iisupc_androidbackup_"))
    try:
        trees: dict[str, Path] = {}
        for label, remote in SOURCES.items():
            if not _remote_exists(remote):
                result.skipped[label] = "folder doesn't exist or isn't readable"
                continue
            parent = work / label
            parent.mkdir()
            pulled = adb_command("pull", remote, str(parent), timeout=PULL_TIMEOUT)
            folder = parent / PurePosixPath(remote).name
            if pulled.returncode != 0 and not folder.is_dir():
                result.skipped[label] = (pulled.stderr.strip().splitlines() or ["adb pull failed"])[-1][:120]
                continue
            trees[label] = folder
        if not trees:
            raise AndroidBackupError("Nothing could be read from the VM: " + result.summary())
        write_archive(destination, trees, result)
    except OSError as exc:
        raise AndroidBackupError(f"Backup failed:\n{exc}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


def restore_backup(source: Path) -> dict[str, str]:
    """Pushes the archive's files back into the VM, replacing same-named
    files and leaving everything else alone. Returns {label: outcome}.
    iiSU is force-stopped first so it doesn't rewrite files mid-restore;
    it needs a restart (Stop, Open) afterwards to pick the data up."""
    inspect_archive(source)  # raises on a bad archive before touching the VM
    ready, detail = adb_device_ready()
    if not ready:
        raise AndroidBackupError(detail or "The Android VM isn't running. Open Community-iiSU-PC first.")

    work = Path(tempfile.mkdtemp(prefix="iisupc_androidrestore_"))
    outcomes: dict[str, str] = {}
    try:
        folders = extract_archive(source, work)
        adb_command("shell", "am", "force-stop", IISU_PACKAGE, timeout=30)
        for label, folder in folders.items():
            remote = SOURCES[label]
            adb_command("shell", f"mkdir -p {android_remote_quote(remote)}", timeout=30)
            # "/." pushes the folder's contents rather than the folder itself
            pushed = adb_command("push", str(folder) + "/.", remote, timeout=PULL_TIMEOUT)
            if pushed.returncode == 0:
                outcomes[label] = "restored"
            else:
                outcomes[label] = "failed: " + ((pushed.stderr.strip().splitlines() or ["adb push failed"])[-1][:120])
    except OSError as exc:
        raise AndroidBackupError(f"Restore failed:\n{exc}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return outcomes
