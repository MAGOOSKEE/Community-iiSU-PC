"""
Mirrors the real ROM library (config.json's roms_dir, on the Windows/network
side) into the Android VM's storage as lightweight placeholder files, so
iiSU can scan and catalog the whole collection without needing the actual
multi-hundred-MB/GB ROM data pushed into the VM.

Why placeholders work: iiSU only needs a file to exist with the right name
to index it, scrape metadata for it (by filename), and build a launch
Intent referencing it. The actual gameplay never touches that file's
content -- our patched LaunchBridge redirects the launch to the bridge,
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
thousands of files -- what used to be slow was creating each placeholder
with its own adb shell round trip (a `mkdir` and a `dd`, forked fresh
inside the guest, once per file). This instead builds one tar archive
locally (pure local disk I/O, no adb involved) and pushes+extracts it in
a single adb push plus a single `tar xf` inside the guest, so the sync
cost no longer scales with round trips at all -- one archive regardless
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
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import portable_sdk  # noqa: F401 -- imported for its import-time PATH fix (adb), not used directly here
from console_names import load_console_lookup, resolve_console_shortname

CONFIG_PATH = Path(__file__).parent / "config.json"

AVD_SDCARD_ROOT = "/sdcard"
AVD_ROMS_DIRNAME = "Roms"
AVD_ROMS_ROOT = f"{AVD_SDCARD_ROOT}/{AVD_ROMS_DIRNAME}"
FINGERPRINT_NAME = ".sync_fingerprint"
FINGERPRINT_PATH = f"{AVD_ROMS_ROOT}/{FINGERPRINT_NAME}"
PLACEHOLDER_SIZE_KB = 4
AVD_TAR_PUSH_PATH = "/data/local/tmp/sync_library.tar"

IGNORE_TOP_LEVEL = {"folder.ico", "sync.ffs_lock"}


def adb(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], capture_output=True, text=True, check=check)


def scan_library(roms_dir: Path, exact: dict, by_compact: dict) -> tuple[dict[str, list[tuple[str, int, int]]], list[str]]:
    """Walks roms_dir once, grouping recognized console folders' files by
    iiSU short name with (relative path, size, mtime) for each -- both
    what's needed to build the sync archive AND to fingerprint the library
    for change detection come from this one walk."""
    consoles: dict[str, list[tuple[str, int, int]]] = {}
    skipped = []
    for console_folder in sorted(roms_dir.iterdir()):
        if console_folder.name in IGNORE_TOP_LEVEL or not console_folder.is_dir():
            continue

        shortname = resolve_console_shortname(console_folder.name, exact, by_compact)
        if shortname is None:
            skipped.append(console_folder.name)
            continue

        entries = []
        for file_path in console_folder.rglob("*"):
            if file_path.is_dir():
                continue
            rel = file_path.relative_to(console_folder).as_posix()
            st = file_path.stat()
            entries.append((rel, st.st_size, int(st.st_mtime)))
        entries.sort()
        consoles.setdefault(shortname, []).extend(entries)

    return consoles, skipped


def fingerprint(roms_dir: Path, consoles: dict[str, list[tuple[str, int, int]]]) -> str:
    """A stable hash of everything that would change what gets synced --
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
    accept a connection -- it doesn't mean /sdcard's own storage stack has
    finished mounting yet, the same class of post-boot race launch_iisu()
    already retries around for the package manager. Calling this too
    early on a genuinely fresh cold boot fails every mkdir under
    AVD_ROMS_ROOT with "No such file or directory" since /sdcard itself
    isn't there yet; an already-running AVD (the common case -- this runs
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
    it there with a plain `tar xf` recreates the whole Roms/ tree -- tar
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
        print("The AVD's storage never became available -- skipping this sync, the next start will retry.")
        sys.exit(1)

    exact, by_compact = load_console_lookup()
    consoles, skipped = scan_library(roms_dir, exact, by_compact)

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
        print(f"Library unchanged ({file_count} file(s) across {len(consoles)} console(s)) -- skipping resync.")
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
    # doesn't leave its old placeholders behind -- tar only ever adds/
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
