"""Backs up and restores the files iiSU keeps in its shared media folder
inside the Android VM (downloaded artwork and media), so a reinstall or a
rebuilt VM doesn't make it download all of that again. The existing Backup &
Restore page only covers this project's config files.

NOT covered: iiSU's private in-app data (its settings, favorites, play
history), which lives in Android's protected app storage that adb can't
read on a non-debuggable app without root.

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
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from services.android_storage_service import adb_command, adb_device_ready, android_remote_quote
from shared import vm_storage

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


def error_line(stderr: str, stdout: str = "") -> str:
    """The most useful line of an adb failure. adb prints a progress line
    last, which is useless as an error message, so prefer a line that says
    error/failed, falling back to the last line of anything."""
    lines = [line.strip() for line in (stderr + "\n" + stdout).splitlines() if line.strip()]
    for line in reversed(lines):
        if "error" in line.lower() or "failed" in line.lower():
            return line[:160]
    return (lines[-1][:160] if lines else "adb push failed")


def restore_backup(source: Path) -> dict[str, str]:
    """Copies the archive's files back into the VM, replacing same-named
    files and leaving everything else alone. Returns {label: outcome}.

    Files go over as tar chunks (about 256 MB each) pushed to
    /data/local/tmp and extracted inside the VM, the way sync_library.py
    moves the ROM placeholders, never file by file or as one huge stream.
    Both failed on a real 2 GB library (seen live): thousands of per-file
    pushes, and a single 2 GB push, each dropped the adb link partway. A
    dropped link now costs one retry of one chunk.

    iiSU is disabled while the files are written and re-enabled afterwards
    (always, even on failure). A force-stop isn't enough: iiSU is the Home
    app, so Android relaunches it within a second and it starts rewriting
    its own assets folder. It needs a restart (Stop, Open) afterwards to
    pick the data up."""
    inspect_archive(source)  # raises on a bad archive before touching the VM
    ready, detail = adb_device_ready()
    if not ready:
        raise AndroidBackupError(detail or "The Android VM isn't running. Open Community-iiSU-PC first.")

    work = Path(tempfile.mkdtemp(prefix="iisupc_androidrestore_"))
    outcomes: dict[str, str] = {}
    try:
        folders = extract_archive(source, work)
        adb_command("shell", "pm", "disable-user", IISU_PACKAGE, timeout=30)
        try:
            for label, folder in folders.items():
                outcomes[label] = _restore_folder(label, folder, work)
        finally:
            adb_command("shell", "pm", "enable", IISU_PACKAGE, timeout=30)
    except OSError as exc:
        raise AndroidBackupError(f"Restore failed:\n{exc}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return outcomes


CHUNK_BYTES = 256 * 1024 * 1024
CHUNK_ATTEMPTS = 3


def plan_chunks(folder: Path, chunk_bytes: int = CHUNK_BYTES) -> list[list[Path]]:
    """Groups the folder's files into lists of roughly chunk_bytes each (a
    file bigger than that goes alone), in a stable order."""
    chunks: list[list[Path]] = []
    current: list[Path] = []
    size = 0
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        file_size = path.stat().st_size
        if current and size + file_size > chunk_bytes:
            chunks.append(current)
            current, size = [], 0
        current.append(path)
        size += file_size
    if current:
        chunks.append(current)
    return chunks


def build_restore_tar(folder: Path, files: list[Path], tar_path: Path) -> int:
    """Tars the given files with paths relative to folder, so extracting
    inside the destination reproduces the layout. Returns the file count."""
    with tarfile.open(tar_path, "w") as tar:
        for path in files:
            tar.add(path, arcname=path.relative_to(folder).as_posix(), recursive=False)
    return len(files)


def _restore_chunk(remote: str, remote_tar: str, tar_path: Path) -> str | None:
    """Pushes and extracts one chunk. Returns None on success or the error."""
    try:
        pushed = adb_command("push", str(tar_path), remote_tar, timeout=PULL_TIMEOUT)
        if pushed.returncode != 0:
            return error_line(pushed.stderr, pushed.stdout)
        extract = (
            f"mkdir -p {android_remote_quote(remote)} && cd {android_remote_quote(remote)} "
            f"&& tar xf {android_remote_quote(remote_tar)}"
        )
        extracted = adb_command("shell", extract, timeout=PULL_TIMEOUT)
        if extracted.returncode != 0:
            return error_line(extracted.stderr, extracted.stdout)
        return None
    finally:
        adb_command("shell", f"rm -f {android_remote_quote(remote_tar)}", timeout=60)


def _restore_folder(label: str, folder: Path, work: Path, chunk_bytes: int = CHUNK_BYTES) -> str:
    """Restores one source in chunks. Each chunk is pushed, extracted and
    deleted before the next, so the VM only ever holds one chunk's tar on top
    of the files themselves, and a dropped adb link (seen live on a 2 GB
    library) costs one retry of one chunk, not the whole restore. Extraction
    only replaces same-named files, so retrying or re-running is harmless."""
    remote = SOURCES[label]
    remote_tar = f"/data/local/tmp/iisupc_restore_{label}.tar"
    chunks = plan_chunks(folder, chunk_bytes)
    if not chunks:
        return "restored"

    total_kb = sum(p.stat().st_size for chunk in chunks for p in chunk) // 1024
    biggest_kb = max(sum(p.stat().st_size for p in chunk) for chunk in chunks) // 1024
    needed_kb = total_kb + biggest_kb + 64 * 1024
    usage = vm_storage.parse_df(adb_command("shell", "df -h /data", timeout=30).stdout)
    if usage is not None and usage.avail_kb < needed_kb:
        return f"failed: not enough free space in the VM ({vm_storage.describe(usage)}); needs about {needed_kb // 1024} MB"

    for index, files in enumerate(chunks, start=1):
        tar_path = work / f"restore_{label}_{index}.tar"
        build_restore_tar(folder, files, tar_path)
        error = None
        for attempt in range(CHUNK_ATTEMPTS):
            if attempt:
                adb_command("wait-for-device", timeout=120)
            error = _restore_chunk(remote, remote_tar, tar_path)
            if error is None:
                break
        tar_path.unlink(missing_ok=True)
        if error is not None:
            return f"failed: part {index} of {len(chunks)}: {error}"
    return "restored"
