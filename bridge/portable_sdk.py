"""
Self-contained, portable Android SDK + AVD storage under the bridge folder
itself (android-sdk-portable/), so Community-iiSU-PC never depends on wherever
Android Studio happened to install its SDK/AVD.

Why: emulator.exe launched against the system-wide install under
%LOCALAPPDATA%\\Android\\Sdk has been observed to intermittently fail with
"Broken AVD system path" / "not a valid directory" for the AVD's system
image, even with ANDROID_SDK_ROOT passed explicitly and the files verified
present and readable moments later. The one thing every failure had in
common was the path living under the current user's AppData\\Local -- a
location whose visibility isn't always consistent across processes on
Windows (AppData redirection, indexing, and sync clients are the usual
suspects, though the exact cause here was never pinned down). Everything
the emulator needs is copied once into a plain folder here instead, on
the same drive as this project, which isn't subject to whatever that was.

This only ever *copies* from the existing installation (never modifies
it), and only does the copy once -- subsequent launches see the portable
copy already in place and skip straight to using it.

Also puts platform-tools (adb) on this process's own PATH as an
import-time side effect (see _prepend_platform_tools_to_path()) -- every
script in this project that shells out to a bare `adb` command assumes
it's resolvable via PATH, which is only true by accident if the machine
happens to already have some other Android SDK installed. Confirmed live:
a fresh machine with no prior Android tooling failed setup outright with
FileNotFoundError the moment it tried "adb devices", despite the portable
copy it needed sitting right there on disk.
"""

import os
import shutil
import subprocess
from pathlib import Path

PORTABLE_ROOT = Path(__file__).parent / "android-sdk-portable"
PORTABLE_SDK = PORTABLE_ROOT / "sdk"
PORTABLE_AVD_HOME = PORTABLE_ROOT / "avd-home"


def _prepend_platform_tools_to_path() -> None:
    """Every caller that already imports this module for its constants --
    which is effectively everywhere that talks to the AVD -- gets adb
    resolvable for free this way, instead of each one needing to remember
    to wire this up itself. A no-op if the portable copy hasn't been
    bootstrapped yet (platform-tools won't exist there, so Windows just
    skips over it during PATH resolution like any other missing entry),
    and safe to call more than once (checked against the current PATH
    first, so this doesn't grow PATH on repeated imports)."""
    platform_tools = str(PORTABLE_SDK / "platform-tools")
    current = os.environ.get("PATH", "")
    if platform_tools not in current.split(os.pathsep):
        os.environ["PATH"] = platform_tools + os.pathsep + current


_prepend_platform_tools_to_path()


def _robocopy(src: Path, dst: Path, exclude_dirs: list[str] | None = None) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    args = ["robocopy", str(src), str(dst), "/E", "/R:2", "/W:2", "/NFL", "/NDL", "/NJH", "/NJS"]
    if exclude_dirs:
        args += ["/XD", *exclude_dirs]
    result = subprocess.run(args, capture_output=True, text=True)
    # robocopy's exit codes 0-7 all mean some degree of success (a bitmask
    # of what it did); 8+ means a real failure.
    if result.returncode >= 8:
        raise RuntimeError(
            f"robocopy {src} -> {dst} failed (code {result.returncode}):\n{result.stdout}\n{result.stderr}"
        )


def _find_real_avd_dir(avd_name: str) -> Path | None:
    candidate = Path.home() / ".android" / "avd" / f"{avd_name}.avd"
    return candidate if candidate.is_dir() else None


def _find_real_avd_ini(avd_name: str) -> Path | None:
    candidate = Path.home() / ".android" / "avd" / f"{avd_name}.ini"
    return candidate if candidate.is_file() else None


# Android's own "medium_phone" device profile (what `android emulator
# create` uses -- see sdk_bootstrap.py) turns on a removable SD card by
# default, but nothing ever creates the sdcard.img file it needs: the
# emulator doesn't auto-create one, so this device permanently shows a
# "mounted" SD card slot with no actual backing storage. Nothing in this
# project uses it (ROM placeholders live under internal storage,
# /sdcard/Roms, which is unrelated), so it's disabled outright rather than
# creating a throwaway image just to satisfy it.
CONFIG_INI_STATIC_OVERRIDES = {
    "hw.sdCard": "no",
}


def patch_config_ini(config_ini: Path, force_cold_boot: bool = True) -> None:
    """Applies CONFIG_INI_STATIC_OVERRIDES plus fastboot.forceColdBoot,
    which is the one entry re-patched on every single start (not just the
    one-time bootstrap copy) -- see start_iisu_pc.py's boot-fingerprint
    check, which decides force_cold_boot each run. This is belt-and-
    suspenders alongside the matching -no-snapshot/no launch flag in
    start_iisu_pc.py, in case anything ever launches this AVD another
    way."""
    if not config_ini.is_file():
        return
    overrides = {**CONFIG_INI_STATIC_OVERRIDES, "fastboot.forceColdBoot": "yes" if force_cold_boot else "no"}
    lines = config_ini.read_text(encoding="utf-8").splitlines()
    seen = set()
    new_lines = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else None
        if key in overrides:
            new_lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in overrides.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")
    config_ini.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def set_quickboot_autosave(avd_dir: Path, enabled: bool) -> None:
    """The emulator rewrites its own quickbootChoice.ini on exit based on
    its current save-on-exit preference, independent of any launch flag:
    a graceful `adb emu kill` only saves a snapshot on exit if this file's
    saveOnExit was left true beforehand. Pinning it explicitly right
    before every launch (not just once) is what actually guarantees the
    outcome this run wants, regardless of what the previous run's exit
    wrote here. enabled=True is what makes quick resume possible at all --
    see start_iisu_pc.py's boot-fingerprint check for when a saved
    snapshot is trusted for the *next* start versus ignored in favor of a
    fresh boot."""
    value = "true" if enabled else "false"
    (avd_dir / "quickbootChoice.ini").write_text(f"saveOnExit = {value}\n", encoding="utf-8")


def _read_image_sysdir(avd_dir: Path) -> str | None:
    config_ini = avd_dir / "config.ini"
    if not config_ini.is_file():
        return None
    for line in config_ini.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("image.sysdir.1="):
            return line.split("=", 1)[1].strip()
    return None


def is_bootstrapped(avd_name: str) -> bool:
    return (
        (PORTABLE_SDK / "emulator" / "emulator.exe").is_file()
        and (PORTABLE_SDK / "platform-tools" / "adb.exe").is_file()
        and (PORTABLE_AVD_HOME / f"{avd_name}.avd" / "config.ini").is_file()
    )


def ensure_portable_sdk(avd_name: str, source_sdk_root: Path) -> dict:
    """Copies the emulator binaries, the AVD's system image, and the AVD's
    own config/userdata into android-sdk-portable/ (skipping anything
    already copied), and returns the environment overrides
    (ANDROID_SDK_ROOT, ANDROID_HOME, ANDROID_AVD_HOME) to launch emulator.exe
    with, so it uses this portable copy instead of the system-wide install."""
    # emulator.exe's own SDK-root validity check requires a platform-tools
    # subdirectory to exist alongside emulator/ and system-images/ -- without
    # it, the root is rejected as invalid regardless of whether the
    # requested AVD's system image is actually there.
    portable_emulator = PORTABLE_SDK / "emulator" / "emulator.exe"
    if not portable_emulator.is_file():
        source_emulator_dir = source_sdk_root / "emulator"
        print(f"[bootstrap] copying emulator ({source_emulator_dir} -> {PORTABLE_SDK / 'emulator'}), one-time, ~1GB...")
        _robocopy(source_emulator_dir, PORTABLE_SDK / "emulator")

    portable_adb = PORTABLE_SDK / "platform-tools" / "adb.exe"
    if not portable_adb.is_file():
        source_platform_tools_dir = source_sdk_root / "platform-tools"
        print(f"[bootstrap] copying platform-tools ({source_platform_tools_dir} -> {PORTABLE_SDK / 'platform-tools'}), one-time...")
        _robocopy(source_platform_tools_dir, PORTABLE_SDK / "platform-tools")

    real_avd_dir = _find_real_avd_dir(avd_name)
    sysdir = _read_image_sysdir(real_avd_dir) if real_avd_dir else None
    if sysdir is None and not is_bootstrapped(avd_name):
        raise RuntimeError(f"Could not read image.sysdir.1 from {real_avd_dir / 'config.ini' if real_avd_dir else '?'}")

    if sysdir:
        portable_image_dir = PORTABLE_SDK / sysdir
        if not (portable_image_dir / "system.img").is_file():
            source_image_dir = source_sdk_root / sysdir
            print(f"[bootstrap] copying system image ({source_image_dir} -> {portable_image_dir}), one-time, several GB...")
            _robocopy(source_image_dir, portable_image_dir)

    portable_avd_dir = PORTABLE_AVD_HOME / f"{avd_name}.avd"
    if not portable_avd_dir.is_dir():
        if real_avd_dir is None:
            raise RuntimeError(f"No existing AVD '{avd_name}' found under ~/.android/avd to copy from.")
        print(f"[bootstrap] copying AVD config ({real_avd_dir} -> {portable_avd_dir}), one-time...")
        # Snapshots are just a quickboot resume cache, not needed for
        # correctness -- skipping them saves a couple of GB and the
        # emulator just does a normal boot instead of a quickboot resume
        # the first time on the portable copy.
        _robocopy(real_avd_dir, portable_avd_dir, exclude_dirs=["snapshots"])
        patch_config_ini(portable_avd_dir / "config.ini", force_cold_boot=True)
        real_avd_ini = _find_real_avd_ini(avd_name)
        PORTABLE_AVD_HOME.mkdir(parents=True, exist_ok=True)
        portable_ini = PORTABLE_AVD_HOME / f"{avd_name}.ini"
        if real_avd_ini:
            shutil.copyfile(real_avd_ini, portable_ini)
        # Fix up the copied .ini's path fields to point at the portable
        # location instead of the original ~/.android/avd one.
        portable_ini.write_text(
            "avd.ini.encoding=UTF-8\n"
            f"path={portable_avd_dir}\n"
            f"path.rel=avd-home/{avd_name}.avd\n"
            "target=android-36\n",
            encoding="utf-8",
        )

    sdk_root_str = str(PORTABLE_SDK)
    return {
        "ANDROID_SDK_ROOT": sdk_root_str,
        "ANDROID_HOME": sdk_root_str,
        "ANDROID_AVD_HOME": str(PORTABLE_AVD_HOME),
    }
