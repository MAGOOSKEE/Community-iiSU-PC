"""
Completely removes everything Setup.bat and day-to-day use create on this
machine, so a fresh Setup.bat run afterward starts truly from scratch --
no reused SDK download, no reused AVD, no leftover config.

Stops the AVD/bridge first (if running) via bridge/stop_iisu_pc.py, then
removes:
  - bridge/'s generated state: the portable SDK+AVD copy, config.json,
    caches, the extracted icon, and the last emulator.log/bridge.log/stop.log
  - installer/'s generated state: its own SDK download, the patch
    keystore, the preserved build-tools copy, working directories
  - the actual AVD(s) under ~/.android/avd/ (and the stray per-AVD log
    directories the emulator leaves alongside it -- see NOTE below)
  - the desktop shortcut

Deliberately does NOT touch:
  - installer/input/*.apk -- that's your own supplied file, not
    something this project installed
  - installer/tools/apktool.jar -- a bundled project asset, not
    generated state
  - %LOCALAPPDATA%\\Android\\Sdk -- see the printed note at the end for
    why this is left alone by default

NOTE on ~/.android/avd/medium_phone.avd: sdk_bootstrap.py creates the
AVD under Android's own hardcoded device-profile name first and renames
it afterward (`android emulator create` doesn't support naming it
directly) -- an interrupted first-time setup can leave that intermediate
name behind before the rename happens, so it's cleaned up defensively
alongside whatever the real configured name is.

Usage:
    python uninstall.py [--yes]
"""

import shutil
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


def remove_path(path: Path, attempts: int = 5, delay: float = 1.0) -> int:
    """Removes a file or directory tree, retrying briefly on a locked
    file -- a process that was just stopped doesn't always release its
    handles the instant it exits, which can otherwise leave a chunk of a
    large directory tree behind on the first attempt. Returns the size
    reclaimed, 0 if the path didn't exist or couldn't be removed."""
    if not path.exists():
        return 0
    size = dir_size(path)
    for attempt in range(attempts):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return size
        except OSError:
            if attempt == attempts - 1:
                print(f"  ! couldn't remove {path} -- still locked, remove it by hand once nothing's using it")
                return 0
            time.sleep(delay)
    return 0


def stop_running_instance() -> None:
    """Run first, before anything else is deleted -- stop_iisu_pc.py
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
        print(f"[uninstall] couldn't run a clean stop ({e}) -- falling back to a process sweep")
        # Matched by command line, not by bare image name -- taskkill /IM
        # emulator.exe (or qemu-system-x86_64.exe) would also take down an
        # unrelated Android Studio emulator instance or another qemu-based
        # tool on the same PC. Every process this project launches runs out
        # of android-sdk-portable/, which scopes this to just this AVD (see
        # the matching, normally-used sweep in stop_iisu_pc.py).
        import stop_iisu_pc
        stop_iisu_pc.kill_by_cmdline_match("android-sdk-portable")


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
    """Every path a full uninstall removes -- used both for the preview
    printed before confirmation and for the actual removal, so the two
    can never drift out of sync with each other."""
    targets = [
        BRIDGE_DIR / "android-sdk-portable",
        BRIDGE_DIR / "config.json",
        BRIDGE_DIR / ".path_cache.json",
        BRIDGE_DIR / ".runtime_state.json",
        BRIDGE_DIR / "emulator.log",
        BRIDGE_DIR / "bridge.log",
        BRIDGE_DIR / "stop.log",
        BRIDGE_DIR / ".iisu_icon.ico",
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
    except Exception:
        targets.append(Path.home() / "Desktop" / "iiSU-PC.lnk")

    return targets


def print_preview(targets: list[Path]) -> None:
    """Sizes everything up front and shows it before the confirmation
    prompt, instead of only finding out how much got reclaimed after it's
    already gone -- makes the "type yes" prompt an informed decision
    rather than a leap of faith."""
    existing = [path for path in targets if path.exists()]
    if not existing:
        print("Nothing to remove -- this already looks like a clean slate.\n")
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
    print("=== iiSU-PC uninstall ===\n")
    print("This removes the Android VM, its SDK, your bridge config, the signing")
    print("keystore, and the desktop shortcut. It does NOT touch your ROM library,")
    print("your PC emulators, or the iiSU APK you supplied in installer/input/.\n")

    avd_name = detect_avd_name()
    targets = collect_targets(avd_name)
    print_preview(targets)

    if "--yes" not in sys.argv:
        answer = input("Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("Cancelled -- nothing was removed.")
            return

    stop_running_instance()

    print("\n[uninstall] removing...")
    reclaimed = sum(remove_path(path) for path in targets)

    print(f"\n=== Done -- reclaimed {reclaimed / 1e9:.1f} GB ===")
    print(f"Kept: installer/input/*.apk (your own file) and installer/tools/apktool.jar (a project asset).")
    print(
        "\nNot touched: %LOCALAPPDATA%\\Android\\Sdk. Depending on how the SDK\n"
        "downloader's underlying tool resolves its install root, packages can end\n"
        "up there instead of (or alongside) installer/android-sdk/ -- if you don't\n"
        "have a real Android Studio install of your own and want that reclaimed\n"
        "too, check for build-tools/34.0.0 and\n"
        "system-images/android-36/google_apis_playstore/x86_64 there before\n"
        "deleting it, since a real install would have those same packages for an\n"
        "unrelated reason."
    )


if __name__ == "__main__":
    main()
