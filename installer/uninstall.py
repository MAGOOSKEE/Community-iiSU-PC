"""
Completely removes everything Setup.bat and day-to-day use create on this
machine, so a fresh Setup.bat run afterward starts truly from scratch,
no reused SDK download, no reused AVD, no leftover config.

Stops the AVD/bridge first (if running) via bridge/stop_iisu_pc.py, then
removes:
  - bridge/'s generated state: the portable SDK+AVD copy, config.json and
    the other settings files the Manager writes (Windows apps, dedupe
    exceptions), caches, the boot fingerprint, the installed-iiSU record,
    the extracted icon, and every log it keeps
  - installer/'s generated state: its own SDK download, the patch
    keystore, the preserved build-tools copy, working directories
  - the actual AVD(s) under ~/.android/avd/ (and the stray per-AVD log
    directories the emulator leaves alongside it, see NOTE below)
  - the desktop shortcut

Deliberately does NOT touch:
  - installer/input/*.apk, that's your own supplied file, not
    something this project installed
  - bridge/iidb/, the saved artwork library, which is kept outside the VM
    precisely so it outlives one, and restore_safety/, the copies of the
    settings a restore overwrote (both listed as kept at the end)
  - installer/tools/apktool.jar, a bundled project asset, not
    generated state
  - %LOCALAPPDATA%\\Android\\Sdk, see the printed note at the end for
    why this is left alone by default

NOTE on ~/.android/avd/medium_phone.avd: sdk_bootstrap.py creates the
AVD under Android's own hardcoded device-profile name first and renames
it afterward (`android emulator create` doesn't support naming it
directly), an interrupted first-time setup can leave that intermediate
name behind before the rename happens, so it's cleaned up defensively
alongside whatever the real configured name is.

Usage:
    python uninstall.py [--yes]
"""

import os
import shutil
import stat
import sys
import time
from pathlib import Path

INSTALLER_DIR = Path(__file__).parent
PROJECT_ROOT = INSTALLER_DIR.parent
BRIDGE_DIR = PROJECT_ROOT / "bridge"

sys.path.insert(0, str(INSTALLER_DIR))
from sdk_bootstrap import DEVICE_PROFILE
from setup_wizard import DEFAULT_AVD_NAME

sys.path.insert(0, str(BRIDGE_DIR))


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _unlink(path: Path) -> None:
    """Deletes one file, clearing its read-only flag first if that is what
    refused it: Windows won't delete a read-only file, and the emulator
    creates the AVD's data/misc/pstore/pstore.bin read-only, which left the
    whole android-sdk-portable/ folder behind on every uninstall."""
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def _remove_tree_best_effort(root: Path) -> list[Path]:
    """Deletes everything under root bottom-up, collecting whatever
    couldn't be removed instead of aborting the whole operation the
    moment one locked file is hit the way a bare shutil.rmtree() would,
    a single still-open handle deep inside a multi-GB tree (android-sdk-
    portable/ in particular, several GB across thousands of files) should
    never be able to leave everything else in that tree behind too,
    silently, as a partial install that only surfaces as a confusing,
    unrelated-looking failure the next time Setup runs."""
    locked: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
        current = Path(dirpath)
        for filename in filenames:
            file_path = current / filename
            try:
                _unlink(file_path)
            except OSError:
                locked.append(file_path)
        try:
            current.rmdir()
        except OSError:
            locked.append(current)
    return locked


def remove_path(path: Path, attempts: int = 5, delay: float = 1.0) -> int:
    """Removes a file or directory tree, retrying briefly on a locked
    file, a process that was just stopped doesn't always release its
    handles the instant it exits, which can otherwise leave a chunk of a
    large directory tree behind on the first attempt. Returns the size
    actually reclaimed, which can be less than the full size if some of
    it is still locked after every retry."""
    if not path.exists():
        return 0
    size = dir_size(path)
    locked: list[Path] = [path]
    for attempt in range(attempts):
        if path.is_file():
            try:
                _unlink(path)
                locked = []
            except OSError:
                locked = [path]
        else:
            locked = _remove_tree_best_effort(path)
        if not locked:
            return size
        if attempt < attempts - 1:
            time.sleep(delay)

    remaining = dir_size(path) if path.exists() else 0
    print(f"  ! {len(locked)} item(s) under {path} are still locked, close whatever's using them and remove by hand:")
    for locked_path in locked[:10]:
        print(f"      {locked_path}")
    if len(locked) > 10:
        print(f"      ... and {len(locked) - 10} more")
    return size - remaining


def stop_running_instance() -> None:
    """Run first, before anything else is deleted, stop_iisu_pc.py
    needs bridge/config.json (for the real avd_name) and
    .runtime_state.json (for the tracked PIDs) to do a clean shutdown,
    both of which this script is about to remove."""
    if not (BRIDGE_DIR / "config.json").is_file() and not (BRIDGE_DIR / ".runtime_state.json").is_file():
        return
    print("[uninstall] stopping the AVD and bridge (if running)...")
    try:
        import stop_iisu_pc
        stop_iisu_pc.main()
    except Exception as e:
        print(f"[uninstall] couldn't run a clean stop ({e}), falling back to a process sweep")
        # Matched by command line, not by bare image name, taskkill /IM
        # emulator.exe (or qemu-system-x86_64.exe) would also take down an
        # unrelated Android Studio emulator instance or another qemu-based
        # tool on the same PC. Every process this project launches runs out
        # of android-sdk-portable/, which scopes this to just this AVD (see
        # the matching, normally-used sweep in stop_iisu_pc.py).
        import stop_iisu_pc
        stop_iisu_pc.kill_by_cmdline_match(stop_iisu_pc.INSTALL_EMULATOR_NEEDLE)


def detect_avd_name() -> str:
    config_path = BRIDGE_DIR / "config.json"
    if config_path.is_file():
        try:
            import json
            return json.loads(config_path.read_text(encoding="utf-8")).get("avd_name", DEFAULT_AVD_NAME)
        except Exception:
            pass
    return DEFAULT_AVD_NAME


def collect_targets(avd_name: str) -> list[Path]:
    """Every path a full uninstall removes, used both for the preview
    printed before confirmation and for the actual removal, so the two
    can never drift out of sync with each other."""
    targets = [
        BRIDGE_DIR / "android-sdk-portable",
        BRIDGE_DIR / "config.json",
        BRIDGE_DIR / ".path_cache.json",
        BRIDGE_DIR / ".runtime_state.json",
        BRIDGE_DIR / ".boot_fingerprint.json",
        BRIDGE_DIR / ".iisu_installed.json",
        BRIDGE_DIR / "windows_apps.json",
        BRIDGE_DIR / "windows_stubs",
        BRIDGE_DIR / "dedupe_exceptions.json",
        BRIDGE_DIR / "cache",
        BRIDGE_DIR / "emulator.log",
        BRIDGE_DIR / "bridge.log",
        BRIDGE_DIR / "stop.log",
        BRIDGE_DIR / "setup.log",
        BRIDGE_DIR / "launch_history.log",
        BRIDGE_DIR / "bridge_debug.log",
        BRIDGE_DIR / "manager_debug.log",
        BRIDGE_DIR / ".iisu_icon.ico",
        BRIDGE_DIR / ".iisu_icon.png",
        BRIDGE_DIR / "_icon_extract_tmp",
        INSTALLER_DIR / "android-sdk",
        INSTALLER_DIR / "_work",
        INSTALLER_DIR / "_cmdline_tools_extract",
        INSTALLER_DIR / "commandlinetools.zip",
        INSTALLER_DIR / "keystore",
        INSTALLER_DIR / "tools" / "build-tools",
    ]

    avd_home = Path.home() / ".android" / "avd"
    for name in {avd_name, DEVICE_PROFILE}:
        targets.append(avd_home / f"{name}.avd")
        targets.append(avd_home / f"{name}.ini")
        # The emulator's own per-AVD scratch/log directory, separate from
        # the *.avd config folder itself (e.g. ~/.android/iisuwin/).
        targets.append(Path.home() / ".android" / name)

    try:
        import create_shortcut
        targets.append(create_shortcut.desktop_dir() / create_shortcut.SHORTCUT_NAME)
        targets.append(create_shortcut.desktop_dir() / create_shortcut.MANAGER_SHORTCUT_NAME)
        if sys.platform != "win32":
            apps_dir = Path.home() / ".local" / "share" / "applications"
            targets.append(apps_dir / create_shortcut.SHORTCUT_NAME)
            targets.append(apps_dir / create_shortcut.MANAGER_SHORTCUT_NAME)
    except Exception:
        fallback_ext = ".lnk" if sys.platform == "win32" else ".desktop"
        targets.append(Path.home() / "Desktop" / f"Community-iiSU-PC{fallback_ext}")

    return targets


def kept_user_data() -> list[tuple[Path, str]]:
    """The folders of your own data an uninstall leaves in place, the ones
    that exist."""
    candidates = [
        (BRIDGE_DIR / "iidb", "your saved artwork library"),
        (PROJECT_ROOT / "restore_safety", "copies of the settings a restore overwrote"),
    ]
    return [(path, what) for path, what in candidates if path.exists()]


def print_preview(targets: list[Path]) -> None:
    """Sizes everything up front and shows it before the confirmation
    prompt, instead of only finding out how much got reclaimed after it's
    already gone, makes the "type yes" prompt an informed decision
    rather than a leap of faith."""
    existing = [path for path in targets if path.exists()]
    if not existing:
        print("Nothing to remove, this already looks like a clean slate.\n")
        return
    total = 0
    print("This will remove:")
    for path in existing:
        size = dir_size(path)
        total += size
        suffix = f"  ({size / 1e9:.2f} GB)" if size >= 1e8 else ""
        print(f"  - {path}{suffix}")
    print(f"\n~{total / 1e9:.2f} GB will be reclaimed.\n")


def main() -> None:
    print("=== Community-iiSU-PC uninstall ===\n")
    print("This removes the Android VM, its SDK, your bridge config, the signing")
    print("keystore, and the desktop shortcut. It does NOT touch your ROM library,")
    print("your PC emulators, or the iiSU APK you supplied in installer/input/.\n")

    avd_name = detect_avd_name()
    targets = collect_targets(avd_name)
    print_preview(targets)

    if "--yes" not in sys.argv:
        answer = input("Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("Cancelled, nothing was removed.")
            return

    stop_running_instance()

    print("\n[uninstall] removing...")
    reclaimed = sum(remove_path(path) for path in targets)

    print(f"\n=== Done, reclaimed {reclaimed / 1e9:.1f} GB ===")
    print(f"Kept: installer/input/*.apk (your own file) and installer/tools/apktool.jar (a project asset).")
    for path, what in kept_user_data():
        print(f"Kept: {path} ({what}), delete it yourself if you don't want it.")
    print(
        "\nNot touched: %LOCALAPPDATA%\\Android\\Sdk. Depending on how the SDK\n"
        "downloader's underlying tool resolves its install root, packages can end\n"
        "up there instead of (or alongside) installer/android-sdk/, if you don't\n"
        "have a real Android Studio install of your own and want that reclaimed\n"
        "too, check for build-tools/34.0.0 and\n"
        "system-images/android-36/google_apis_playstore/x86_64 there before\n"
        "deleting it, since a real install would have those same packages for an\n"
        "unrelated reason."
    )


if __name__ == "__main__":
    main()
