"""
Bootstraps a self-contained Android SDK (platform-tools, emulator, system
image) and creates a fresh AVD, using Google's modern unified "android" CLI
bundled inside the official "command line tools only" package --
https://developer.android.com/studio#command-tools (the classic
sdkmanager/avdmanager duo is deprecated in this SDK generation, and
avdmanager was confirmed broken against its newer system-image package
format: "Package path is not valid" even for a package sdkmanager itself
lists as installed).

The commandlinetools zip's filename is versioned by Google and isn't a
stable "latest" link -- if COMMANDLINETOOLS_URL below ever 404s, grab the
current one from the URL above and update the constant.

Only creates the AVD and installs SDK packages; it never touches or
bundles iiSU's own APK (see patch_iisu.py for that) or any PC emulator
(the person running this supplies their own, same as bridge/config.json's
search_roots already assumes).
"""

import shutil
import subprocess
import threading
import urllib.request
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SDK_ROOT = SCRIPT_DIR / "android-sdk"

COMMANDLINETOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip"
SYSTEM_IMAGE = "system-images;android-36;google_apis_playstore;x86_64"
BUILD_TOOLS_VERSION = "34.0.0"
BUILD_TOOLS = f"build-tools;{BUILD_TOOLS_VERSION}"
PACKAGES = ["platform-tools", "emulator", SYSTEM_IMAGE, BUILD_TOOLS]
DEVICE_PROFILE = "medium_phone"


def zipalign_exe() -> Path:
    return SDK_ROOT / "build-tools" / BUILD_TOOLS_VERSION / "zipalign.exe"


def apksigner_bat() -> Path:
    return SDK_ROOT / "build-tools" / BUILD_TOOLS_VERSION / "apksigner.bat"


def android_exe() -> Path:
    return SDK_ROOT / "cmdline-tools" / "latest" / "bin" / "android.exe"


def is_sdk_ready() -> bool:
    return (
        (SDK_ROOT / "platform-tools" / "adb.exe").is_file()
        and (SDK_ROOT / "emulator" / "emulator.exe").is_file()
        and (SDK_ROOT / SYSTEM_IMAGE.replace(";", "/") / "system.img").is_file()
        and zipalign_exe().is_file()
        and apksigner_bat().is_file()
    )


def _make_download_reporthook():
    """A urlretrieve reporthook that prints every 10% instead of every
    block -- there'd otherwise be one line per 8KB chunk, and with nothing
    printed at all a ~156MB download over a slow connection looks
    indistinguishable from a hang."""
    state = {"last_reported": -10}

    def reporthook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size <= 0:
            return
        downloaded = min(block_num * block_size, total_size)
        percent = int(downloaded * 100 / total_size)
        if percent >= state["last_reported"] + 10 or percent == 100:
            print(f"[sdk]   ...{percent}% ({downloaded / 1e6:.0f} / {total_size / 1e6:.0f} MB)")
            state["last_reported"] = percent

    return reporthook


def download_commandline_tools() -> Path:
    zip_path = SCRIPT_DIR / "commandlinetools.zip"
    if not zip_path.is_file():
        print("[sdk] downloading Android command-line tools (~156 MB)...")
        urllib.request.urlretrieve(COMMANDLINETOOLS_URL, zip_path, reporthook=_make_download_reporthook())
    return zip_path


def install_commandline_tools() -> None:
    if android_exe().is_file():
        return
    zip_path = download_commandline_tools()
    print("[sdk] extracting command-line tools...")
    extract_dir = SCRIPT_DIR / "_cmdline_tools_extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)

    cmdline_tools_dir = SDK_ROOT / "cmdline-tools"
    cmdline_tools_dir.mkdir(parents=True, exist_ok=True)
    latest_dir = cmdline_tools_dir / "latest"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.move(str(extract_dir / "cmdline-tools"), str(latest_dir))
    shutil.rmtree(extract_dir)


def _package_already_installed(package: str) -> bool:
    if package == SYSTEM_IMAGE:
        return (SDK_ROOT / SYSTEM_IMAGE.replace(";", "/") / "system.img").is_file()
    if package == BUILD_TOOLS:
        return zipalign_exe().is_file() and apksigner_bat().is_file()
    return (SDK_ROOT / package).is_dir()


def _run_with_heartbeat(args: list[str], what: str, interval: float = 15.0) -> None:
    """`android sdk install` gives no output of its own worth showing (see
    the note below on why its exit code isn't trustworthy either), so a
    multi-GB system image install would otherwise sit in total silence for
    several minutes -- indistinguishable from having actually hung. This
    just proves it's still alive."""
    stop = threading.Event()

    def heartbeat() -> None:
        elapsed = 0.0
        while not stop.wait(interval):
            elapsed += interval
            print(f"[sdk]   ...still installing {what} ({int(elapsed)}s elapsed)")

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        subprocess.run(args, capture_output=True, text=True)
    finally:
        stop.set()
        thread.join()


def install_packages() -> None:
    for package in PACKAGES:
        if _package_already_installed(package):
            continue
        print(f"[sdk] installing {package} (this can take a while for the system image, several GB)...")
        # This tool's own exit code is unreliable under non-interactive
        # output redirection (observed non-zero codes on installs that
        # actually succeeded -- likely a progress-bar/non-TTY quirk), so
        # success is checked below by looking for the resulting files
        # rather than trusting the return code.
        _run_with_heartbeat([str(android_exe()), f"--sdk={SDK_ROOT}", "sdk", "install", package], what=package)

    if not is_sdk_ready():
        raise RuntimeError(
            f"SDK package install did not produce the expected files under {SDK_ROOT}. "
            f"Try running `{android_exe()} --sdk={SDK_ROOT} sdk install <package>` manually "
            "for each of platform-tools / emulator / " + SYSTEM_IMAGE + " to see the actual error."
        )


def create_default_avd() -> Path:
    """Creates the AVD in its unavoidable default location
    (~/.android/avd/medium_phone.avd) -- unlike every other tool used here,
    `android emulator create` does not honor ANDROID_AVD_HOME. The caller
    renames it to the configured avd_name and portable_sdk.py (in bridge/)
    then adopts it into a fully portable copy, exactly as if it had been
    made through Android Studio's own AVD wizard and renamed by hand."""
    default_avd_dir = Path.home() / ".android" / "avd" / f"{DEVICE_PROFILE}.avd"
    if not default_avd_dir.is_dir():
        print(f"[sdk] creating a new AVD from the '{DEVICE_PROFILE}' device profile...")
        subprocess.run([str(android_exe()), f"--sdk={SDK_ROOT}", "emulator", "create", DEVICE_PROFILE], capture_output=True, text=True)
        if not default_avd_dir.is_dir():
            raise RuntimeError(f"`android emulator create {DEVICE_PROFILE}` did not produce {default_avd_dir}.")
    return default_avd_dir


def rename_avd(old_name: str, new_name: str) -> Path:
    """Renames a just-created AVD (e.g. medium_phone -> iisuwin) in place
    under ~/.android/avd/. Only touches the AVD's own identity (AvdId,
    displayname, the .ini pointer file) -- hw.device.name inside config.ini
    is deliberately left alone, since that references the real registered
    device profile/skin ('medium_phone'), not the AVD's own name, and
    changing it would break the AVD."""
    avd_root = Path.home() / ".android" / "avd"
    old_avd_dir = avd_root / f"{old_name}.avd"
    new_avd_dir = avd_root / f"{new_name}.avd"
    old_ini = avd_root / f"{old_name}.ini"
    new_ini = avd_root / f"{new_name}.ini"

    if new_avd_dir.is_dir():
        return new_avd_dir
    if not old_avd_dir.is_dir():
        raise RuntimeError(f"Expected {old_avd_dir} to exist before renaming to '{new_name}'.")

    old_avd_dir.rename(new_avd_dir)
    old_ini.rename(new_ini)

    config_ini = new_avd_dir / "config.ini"
    text = config_ini.read_text(encoding="utf-8")
    text = text.replace(f"AvdId={old_name}", f"AvdId={new_name}").replace(
        f"AvdId={old_name.replace('_', ' ').title().replace(' ', '_')}", f"AvdId={new_name}"
    )
    lines = [
        line if not line.startswith("AvdId=") else f"AvdId={new_name}"
        for line in text.splitlines()
    ]
    lines = [
        line if not line.startswith("avd.ini.displayname=") else f"avd.ini.displayname={new_name}"
        for line in lines
    ]
    config_ini.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ini_text = new_ini.read_text(encoding="utf-8")
    ini_text = ini_text.replace(f"{old_name}.avd", f"{new_name}.avd")
    new_ini.write_text(ini_text, encoding="utf-8")

    return new_avd_dir


def ensure_sdk_and_avd(avd_name: str) -> Path:
    """Top-level entry point: makes sure the SDK is installed and an AVD
    named avd_name exists under ~/.android/avd/, ready for
    bridge/portable_sdk.py to adopt into a fully portable copy."""
    install_commandline_tools()
    install_packages()
    create_default_avd()
    return rename_avd(DEVICE_PROFILE, avd_name)
