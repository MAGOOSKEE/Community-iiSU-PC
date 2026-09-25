"""
Mirrors the real ROM library (config.json's roms_dir, on the Windows/network
side) into the Android VM's storage as lightweight placeholder files, so
iiSU can scan and catalog the whole collection without needing the actual
multi-hundred-MB/GB ROM data pushed into the VM.

Why placeholders work: iiSU only needs a file to exist with the right name
to index it, scrape metadata for it (by filename), and build a launch
Intent referencing it. The actual gameplay never touches that file's
content, our patched LaunchBridge redirects the launch to the bridge,
which finds the REAL file under roms_dir by matching filename and hands
that to the real PC emulator. So placeholders can be tiny (a few KB, not
0 bytes, in case iiSU's scanner distrusts empty files) and their content
is irrelevant.

Folder names matter though: iiSU only recognizes a folder as a console if
its name matches one of iiSU's own known console short/long/alternate
names (see console_names.json, extracted from iiSU's own bundled
emuladores_default.json). This script normalizes whatever a console
folder happens to be named on disk (e.g. "Playstation 1") into iiSU's
expected form (e.g. "psx") automatically.

Runs automatically on every start (see start_iisu_pc.py), and skips the
actual rebuild whenever nothing's changed since the last one. For a
large library, walking the real filesystem is fast even for tens of
thousands of files, what used to be slow was creating each placeholder
with its own adb shell round trip (a `mkdir` and a `dd`, forked fresh
inside the guest, once per file). This instead builds one tar archive
locally (pure local disk I/O, no adb involved) and pushes+extracts it in
a single adb push plus a single `tar xf` inside the guest, so the sync
cost no longer scales with round trips at all, one archive regardless
of whether it holds a hundred files or a hundred thousand. The "last synced" signal lives
*inside* the AVD itself (a fingerprint file dropped alongside the
placeholders), not a local cache file here: a local cache would go stale
the moment the AVD gets wiped or rebuilt independently of the real
library changing, silently leaving it with no ROMs at all and no sign
why. Reading the fingerprint back out of the AVD instead means the
"is this still current" question is always answered by what's actually
there, never by a guess about it.
"""

import hashlib
import io
import json
import re
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

import portable_sdk  # noqa: F401; imported for its import-time PATH fix (adb), not used directly here
from console_names import load_console_lookup, resolve_console_shortname

CONFIG_PATH = Path(__file__).parent / "config.json"
WINDOWS_STUBS_DIR = Path(__file__).parent / "windows_stubs"
DEDUPE_EXCEPTIONS_PATH = Path(__file__).parent / "dedupe_exceptions.json"

AVD_SDCARD_ROOT = "/sdcard"
AVD_ROMS_DIRNAME = "Roms"
AVD_ROMS_ROOT = f"{AVD_SDCARD_ROOT}/{AVD_ROMS_DIRNAME}"
FINGERPRINT_NAME = ".sync_fingerprint"
FINGERPRINT_PATH = f"{AVD_ROMS_ROOT}/{FINGERPRINT_NAME}"
PLACEHOLDER_SIZE_KB = 4
AVD_TAR_PUSH_PATH = "/data/local/tmp/sync_library.tar"

IGNORE_TOP_LEVEL = {"folder.ico", "sync.ffs_lock"}

CUE_FILE_LINE = re.compile(r'^\s*FILE\s+"([^"]+)"', re.IGNORECASE)


def _read_text_best_effort(path: Path) -> str:
    """Disc playlists/sheets are usually ASCII but occasionally shipped in
    whatever codepage the ripping tool's locale used, utf-8 first, a
    permissive fallback rather than letting one oddly-encoded file abort
    the whole sync."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def load_dedupe_exceptions() -> set[str]:
    """Keys are "<iiSU shortname>/<path relative to that console folder>",
    e.g. "psx/Gran Turismo 2/Gran Turismo 2.m3u", keyed by the normalized
    shortname rather than whatever the on-disk console folder happens to
    be named, so renaming "PSX" to "Playstation 1" later doesn't silently
    detach the exception from the game it was set for. Opts a specific
    .m3u/.cue out of the disc-collapsing below, iiSU sees the individual
    discs it names as their own entries instead, and the playlist/sheet
    itself is hidden rather than also shown alongside them. For a game like
    Gran Turismo 2, where what an .m3u bundles are actually distinct,
    separately-launchable modes rather than continuation discs of the same
    game, so collapsing them away isn't right the way it is for an
    ordinary multi-disc RPG. Set from the Games > Console page in
    manager.py, one entry per opted-out playlist/sheet, not per console or
    per folder, opting out one game should never affect its neighbors."""
    if not DEDUPE_EXCEPTIONS_PATH.is_file():
        return set()
    try:
        data = json.loads(DEDUPE_EXCEPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data) if isinstance(data, list) else set()


def save_dedupe_exceptions(exceptions: set[str]) -> None:
    DEDUPE_EXCEPTIONS_PATH.write_text(json.dumps(sorted(exceptions), indent=2) + "\n", encoding="utf-8")


def _m3u_referenced_filenames(m3u_path: Path) -> set[str]:
    referenced = set()
    for line in _read_text_best_effort(m3u_path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        referenced.add(PurePosixPath(line.replace("\\", "/")).name)
    return referenced


def _cue_referenced_filenames(cue_path: Path) -> set[str]:
    referenced = set()
    for line in _read_text_best_effort(cue_path).splitlines():
        match = CUE_FILE_LINE.match(line)
        if match:
            referenced.add(PurePosixPath(match.group(1).replace("\\", "/")).name)
    return referenced


def referenced_disc_filenames(
    directory_files: list[Path],
    console_folder: Path | None = None,
    shortname: str | None = None,
    dedupe_exceptions: set[str] | None = None,
) -> set[str]:
    """Filenames that should NOT get their own placeholder, because
    something else in this same folder already accounts for them.

    Ordinarily that's every filename an .m3u playlist or .cue sheet
    references, without this, a 2-disc PS1 game (game.m3u +
    disc1.bin/.cue + disc2.bin/.cue) shows up in iiSU as 5 separate "games"
    instead of one, since the .m3u/.cue itself is what a PC emulator is
    actually pointed at (the tracks/discs it names are matched by filename
    by the launch bridge but never need an iiSU entry of their own).

    A .m3u/.cue listed in dedupe_exceptions (see load_dedupe_exceptions)
    inverts that: it's the playlist/sheet itself that gets excluded instead,
    so its individual discs show up as their own entries and iiSU never
    sees the collapsed, one-entry version at all, for a game like Gran
    Turismo 2, where what iiSU would otherwise show as one game is really
    two distinct, separately-launchable modes.

    Scoped to files matching by name within the same folder only, not
    globally, so unrelated same-named files elsewhere in the library are
    never affected. console_folder/shortname/dedupe_exceptions are optional
    so existing callers that don't care about exceptions keep working
    unchanged."""
    dedupe_exceptions = dedupe_exceptions or set()
    referenced: set[str] = set()
    for file_path in directory_files:
        suffix = file_path.suffix.lower()
        if suffix not in (".m3u", ".cue"):
            continue
        if console_folder is not None and shortname is not None:
            exception_key = f"{shortname}/{file_path.relative_to(console_folder).as_posix()}"
            if exception_key in dedupe_exceptions:
                # Exempted: hide the playlist/sheet itself instead of the
                # discs it names, the opposite of the usual direction.
                referenced.add(file_path.name)
                continue
        if suffix == ".m3u":
            referenced |= _m3u_referenced_filenames(file_path)
        else:
            referenced |= _cue_referenced_filenames(file_path)
    return referenced


def adb(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    # adb.exe is console-subsystem; every caller of this runs from the GUI
    # (pythonw.exe, no console of its own), so without CREATE_NO_WINDOW
    # each call here would flash its own console window.
    return subprocess.run(
        ["adb", *args], capture_output=True, text=True, check=check, creationflags=0x08000000
    )


def scan_library(
    roms_dir: Path, exact: dict, by_compact: dict, dedupe_exceptions: set[str] | None = None
) -> tuple[dict[str, list[tuple[str, int, int]]], list[str]]:
    """Walks roms_dir once, grouping recognized console folders' files by
    iiSU short name with (relative path, size, mtime) for each, both
    what's needed to build the sync archive AND to fingerprint the library
    for change detection come from this one walk."""
    if dedupe_exceptions is None:
        dedupe_exceptions = load_dedupe_exceptions()
    consoles: dict[str, list[tuple[str, int, int]]] = {}
    skipped = []
    for console_folder in sorted(roms_dir.iterdir()):
        if console_folder.name in IGNORE_TOP_LEVEL or not console_folder.is_dir():
            continue

        shortname = resolve_console_shortname(console_folder.name, exact, by_compact)
        if shortname is None:
            skipped.append(console_folder.name)
            continue

        by_directory: dict[Path, list[Path]] = {}
        for file_path in console_folder.rglob("*"):
            if file_path.is_dir():
                continue
            by_directory.setdefault(file_path.parent, []).append(file_path)

        entries = []
        for directory, files in by_directory.items():
            excluded = referenced_disc_filenames(
                files, console_folder=console_folder, shortname=shortname, dedupe_exceptions=dedupe_exceptions
            )
            for file_path in files:
                if file_path.name in excluded:
                    continue
                rel = file_path.relative_to(console_folder).as_posix()
                st = file_path.stat()
                entries.append((rel, st.st_size, int(st.st_mtime)))
        entries.sort()
        consoles.setdefault(shortname, []).extend(entries)

    return consoles, skipped


def scan_windows_stubs() -> list[tuple[str, int, int]]:
    """Windows Apps' .pcgame placeholders live in the install directory
    (see manager.py's WINDOWS_STUBS_DIR), not under roms_dir, so they
    never clutter the real ROM library on disk, scanned separately here
    and merged into the "windows" console's entries so Windows Apps games
    still show up in iiSU."""
    if not WINDOWS_STUBS_DIR.is_dir():
        return []
    entries = []
    for file_path in WINDOWS_STUBS_DIR.glob("*.pcgame"):
        st = file_path.stat()
        entries.append((file_path.name, st.st_size, int(st.st_mtime)))
    return entries


def merge_windows_stubs(consoles: dict[str, list[tuple[str, int, int]]]) -> None:
    """Merges scan_windows_stubs() into consoles["windows"] in place,
    de-duplicating by filename against whatever scan_library() already
    found under a legacy <roms_dir>/windows folder, an install mid-
    migration (see manager.py's _migrate_legacy_windows_stubs) can
    otherwise have the same placeholder in both places."""
    stub_entries = scan_windows_stubs()
    if not stub_entries:
        return
    merged = {name: (name, size, mtime) for name, size, mtime in consoles.get("windows", [])}
    for name, size, mtime in stub_entries:
        merged[name] = (name, size, mtime)
    consoles["windows"] = sorted(merged.values())


def fingerprint(roms_dir: Path, consoles: dict[str, list[tuple[str, int, int]]]) -> str:
    """A stable hash of everything that would change what gets synced,
    which consoles, and each file's relative path/size/mtime."""
    h = hashlib.sha256()
    h.update(str(roms_dir).encode("utf-8"))
    for shortname in sorted(consoles):
        h.update(f"\n#{shortname}\n".encode("utf-8"))
        for rel, size, mtime in consoles[shortname]:
            h.update(f"{rel}|{size}|{mtime}\n".encode("utf-8"))
    return h.hexdigest()


def read_avd_fingerprint() -> str | None:
    result = adb("shell", f"cat {shlex.quote(FINGERPRINT_PATH)}", check=False)
    return result.stdout.strip() or None


def wait_for_external_storage(timeout: float = 60.0) -> bool:
    """adb becoming reachable only means the AVD booted far enough to
    accept a connection, it doesn't mean /sdcard's own storage stack has
    finished mounting yet, the same class of post-boot race launch_iisu()
    already retries around for the package manager. Calling this too
    early on a genuinely fresh cold boot fails every mkdir under
    AVD_ROMS_ROOT with "No such file or directory" since /sdcard itself
    isn't there yet; an already-running AVD (the common case, this runs
    on every start, not just the first) has always been up long enough
    for this to return immediately."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if adb("shell", "test -d /sdcard", check=False).returncode == 0:
            return True
        time.sleep(2)
    return False


def build_placeholder_tar(consoles: dict[str, list[tuple[str, int, int]]], current_fingerprint: str) -> Path:
    """Builds one local tar archive holding every placeholder file (plus the
    fingerprint file) with paths already relative to /sdcard, so extracting
    it there with a plain `tar xf` recreates the whole Roms/ tree, tar
    creates whatever parent directories a member needs, so nothing here has
    to mkdir anything up front. All of this is local disk I/O; nothing here
    talks to the device."""
    placeholder = b"\0" * (PLACEHOLDER_SIZE_KB * 1024)
    tar_path = Path(__file__).parent / "_sync_tmp.tar"
    with tarfile.open(tar_path, "w") as tar:
        for shortname, entries in consoles.items():
            for rel, _size, _mtime in entries:
                info = tarfile.TarInfo(name=f"{AVD_ROMS_DIRNAME}/{shortname}/{rel}")
                info.size = len(placeholder)
                tar.addfile(info, io.BytesIO(placeholder))
        fingerprint_bytes = current_fingerprint.encode("utf-8")
        info = tarfile.TarInfo(name=f"{AVD_ROMS_DIRNAME}/{FINGERPRINT_NAME}")
        info.size = len(fingerprint_bytes)
        tar.addfile(info, io.BytesIO(fingerprint_bytes))
    return tar_path


def main() -> None:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    roms_dir = Path(config["roms_dir"])
    if not roms_dir.is_dir():
        print(f"roms_dir '{roms_dir}' does not exist or isn't reachable.")
        sys.exit(1)

    if not wait_for_external_storage():
        print("The AVD's storage never became available, skipping this sync, the next start will retry.")
        sys.exit(1)

    exact, by_compact = load_console_lookup()
    consoles, skipped = scan_library(roms_dir, exact, by_compact)
    merge_windows_stubs(consoles)

    if skipped:
        print("Skipped folders with no matching iiSU console name (rename the folder, or add it to console_names.NAME_OVERRIDES):")
        for name in skipped:
            print(f"  - {name}")

    if not consoles:
        print("No recognized console folders found under roms_dir; nothing to sync.")
        return

    file_count = sum(len(entries) for entries in consoles.values())
    current_fingerprint = fingerprint(roms_dir, consoles)
    if read_avd_fingerprint() == current_fingerprint:
        print(f"Library unchanged ({file_count} file(s) across {len(consoles)} console(s)), skipping resync.")
        return

    print(f"Building one archive for {len(consoles)} console folder(s), {file_count} placeholder file(s)...")
    tar_path = build_placeholder_tar(consoles, current_fingerprint)

    push_size_mb = tar_path.stat().st_size / 1e6
    print(f"Pushing the archive ({push_size_mb:.1f} MB) to the AVD and extracting it in one shot...")
    push_start = time.time()
    adb("push", str(tar_path), AVD_TAR_PUSH_PATH)
    push_elapsed = max(time.time() - push_start, 0.01)
    print(f"Pushed in {push_elapsed:.1f}s ({push_size_mb / push_elapsed:.1f} MB/s).")
    # rm -rf first so a console removed from the real library (or renamed)
    # doesn't leave its old placeholders behind, tar only ever adds/
    # overwrites, it never removes what a previous sync left there.
    extract_cmd = (
        f"rm -rf {shlex.quote(AVD_ROMS_ROOT)} && "
        f"cd {shlex.quote(AVD_SDCARD_ROOT)} && tar xf {shlex.quote(AVD_TAR_PUSH_PATH)}"
    )
    result = adb("shell", extract_cmd, check=False)
    adb("shell", f"rm -f {shlex.quote(AVD_TAR_PUSH_PATH)}", check=False)
    tar_path.unlink(missing_ok=True)
    if result.returncode != 0:
        print("Extracting the archive on the AVD failed:")
        print(result.stdout)
        print(result.stderr)
        sys.exit(1)

    print("Done. Now hit \"Rescan full library\" in iiSU's Library settings.")


if __name__ == "__main__":
    main()
