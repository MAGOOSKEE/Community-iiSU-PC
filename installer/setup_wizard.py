"""
First-time setup for iiSU-PC: bootstraps a self-contained Android SDK and
AVD (sdk_bootstrap.py), patches a copy of iiSU that you supply yourself
(patch_iisu.py) to redirect its ROM launches to a PC-side bridge, installs
it, and points the bridge/ folder at the result.

This never bundles or redistributes iiSU's own APK -- you need your own
copy of it, same as you'd need for any other APK-patching tool. Drop it
into input/ before running this (see the printed instructions below if
none is found).

Usage:
    python setup_wizard.py
"""

import json
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import sdk_bootstrap
from patch_iisu import patch_apk, validate_iisu_apk

INSTALLER_DIR = Path(__file__).parent
PROJECT_ROOT = INSTALLER_DIR.parent
BRIDGE_DIR = PROJECT_ROOT / "bridge"
INPUT_DIR = INSTALLER_DIR / "input"
KEYSTORE_DIR = INSTALLER_DIR / "keystore"
KEYSTORE_PATH = KEYSTORE_DIR / "iisu-pc.keystore"
KEYSTORE_META_PATH = KEYSTORE_DIR / "keystore.json"
KEY_ALIAS = "iisu-pc"
WORK_DIR = INSTALLER_DIR / "_work"

DEFAULT_AVD_NAME = "iisuwin"
DEFAULT_DISPLAY = {"width": 1920, "height": 1080, "density": 240, "refresh_rate": 144}
# Generous on purpose: without hardware virtualization (Hyper-V/WHPX on
# Windows, or disabled in the BIOS/UEFI) the emulator falls back to pure
# software rendering, and a first cold boot -- creating the userdata
# partition from scratch, not just resuming one -- can genuinely take
# several minutes there instead of well under one.
AVD_BOOT_TIMEOUT = 420
MIN_FREE_DISK_GB = 15

SETUP_STAGES = [
    "Checking prerequisites",
    "Setting up the Android SDK and AVD",
    "Preparing the signing key",
    "Patching iiSU",
    "Copying to the portable AVD",
    "Installing iiSU and redirector stubs",
    "Finishing up",
]

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def find_input_apk() -> Path | None:
    apks = list(INPUT_DIR.glob("*.apk"))
    return apks[0] if apks else None


def check_disk_space() -> None:
    """The installer's own SDK copy and the portable copy under bridge/
    briefly coexist before cleanup_installer_sdk() reclaims the first one,
    so peak usage during setup is well above what either copy needs alone
    -- checked up front so a low-disk failure surfaces in a second, not
    partway through a multi-GB download."""
    usage = shutil.disk_usage(INSTALLER_DIR)
    free_gb = usage.free / 1e9
    if free_gb < MIN_FREE_DISK_GB:
        raise RuntimeError(
            f"Only {free_gb:.1f} GB free on the drive holding {INSTALLER_DIR} -- this setup needs "
            f"about {MIN_FREE_DISK_GB} GB (the SDK/AVD images are briefly duplicated between the "
            "installer's own copy and the portable copy under bridge/ before cleanup). Free up some "
            "space and run this again."
        )
    print(f"[setup] {free_gb:.1f} GB free -- enough room for setup.")


def require_java() -> None:
    if shutil.which("java") is None:
        raise RuntimeError(
            "Java was not found on PATH. This installer needs a JDK (for apktool and key "
            "generation) -- install one (e.g. Eclipse Temurin) and make sure `java` and "
            "`keytool` are on PATH, then run this again."
        )
    if shutil.which("keytool") is None:
        raise RuntimeError("`keytool` was not found on PATH (it ships with any JDK) -- check your Java install includes it.")


def ensure_pillow() -> None:
    """Pillow backs two purely cosmetic features -- the desktop shortcut's
    real extracted icon (create_shortcut.py) and the Manager's Credits
    page avatars (shared/avatars.py) -- both of which already degrade
    gracefully without it (a generic icon, a plain colored circle). That's
    a reasonable fallback for something genuinely unavailable, but not a
    reason to make it the default experience when a one-time `pip install`
    fixes it for good. Never fatal to setup: a failed install here just
    means those two features fall back exactly like they already do."""
    try:
        import PIL  # noqa: F401
        return
    except ImportError:
        pass
    print("[setup] Pillow isn't installed (used for the desktop shortcut's real icon and the")
    print("[setup] Manager's Credits page avatars) -- installing it now...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "pillow"], capture_output=True, text=True
    )
    if result.returncode == 0:
        print("[setup] Pillow installed.")
    else:
        print(f"[setup] couldn't install Pillow automatically -- continuing without it ({result.stderr.strip()[:200]})")


def ensure_keystore() -> tuple[Path, str]:
    """Generates a fresh, locally-unique signing key on first run -- each
    install of this installer gets its own, rather than everyone who runs
    it sharing one embedded in the distributed files."""
    KEYSTORE_DIR.mkdir(parents=True, exist_ok=True)
    if KEYSTORE_PATH.is_file() and KEYSTORE_META_PATH.is_file():
        meta = json.loads(KEYSTORE_META_PATH.read_text(encoding="utf-8"))
        return KEYSTORE_PATH, meta["password"]

    password = secrets.token_hex(16)
    print("[setup] generating a local signing key...")
    result = subprocess.run(
        [
            "keytool", "-genkeypair", "-v",
            "-keystore", str(KEYSTORE_PATH),
            "-alias", KEY_ALIAS,
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-storepass", password, "-keypass", password,
            "-dname", "CN=iiSU-PC, OU=iiSU-PC, O=iiSU-PC, L=Local, S=Local, C=US",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"keytool failed:\n{result.stdout}\n{result.stderr}")

    KEYSTORE_META_PATH.write_text(json.dumps({"password": password}), encoding="utf-8")
    return KEYSTORE_PATH, password


def wait_for_avd(avd_name: str, timeout: float, process: subprocess.Popen | None = None) -> bool:
    """`adb devices` reporting "device" state only means the ADB link is
    up -- it doesn't mean Android's own system services have finished
    starting. `adb install` needs PackageManagerService specifically,
    which can still be initializing well after ADB itself is reachable,
    especially on an AVD's very first-ever cold boot -- it shows up as
    `adb install` failing with "cmd: Can't find service: package" even
    though ADB is already connected. sys.boot_completed is the actual
    signal that the OS is done starting up.

    process, when given, is polled every iteration so a crashed emulator
    is caught in a couple of seconds instead of only after the full
    timeout: without hardware virtualization at all (no WHPX/Hyper-V, and
    no software fallback available either), emulator.exe doesn't run
    slowly -- it exits almost immediately. Waiting out the full multi-
    minute timeout for that case reports a generic "AVD didn't come up"
    that reads identically to a merely-slow software-rendered boot, when
    the actual, more specific and more actionable cause (diagnosable via
    virtualization_diagnostics() below) was knowable in seconds. The
    caller distinguishes the two by checking process.poll() itself once
    this returns False -- still not None means it genuinely just timed
    out while running; not None means it died."""
    deadline = time.time() + timeout
    connected = False
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            return False
        if not connected:
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
            connected = any(line.startswith("emulator-") and "device" in line for line in result.stdout.splitlines())
            if not connected:
                time.sleep(2)
                continue
        boot_check = subprocess.run(["adb", "shell", "getprop", "sys.boot_completed"], capture_output=True, text=True)
        if boot_check.stdout.strip() == "1":
            return True
        time.sleep(2)
    return False


def virtualization_diagnostics() -> dict:
    """Reports the same virtualization state Task Manager's Performance
    tab shows for "Virtualization: Enabled/Disabled" (Win32_Processor's
    VirtualizationFirmwareEnabled -- the CPU/BIOS-level VT-x/AMD-V flag),
    plus whether a hypervisor is actually active right now
    (Win32_ComputerSystem's HypervisorPresent -- true for Hyper-V, WHPX,
    or any other hypervisor, whichever is actually providing acceleration
    -- not tied to one specific named Windows feature). Deliberately not
    Get-WindowsOptionalFeature: querying installed features' state
    requires an elevated PowerShell session, and this needs to work from
    this project's normal, non-admin install/bridge processes. Both False
    values default to False rather than raising if the query itself fails
    for any reason (e.g. WMI unavailable) -- this is a diagnostic aid for
    a *different* failure already in progress, not something that should
    itself become a second failure."""
    result = {"cpu_virtualization_enabled": False, "hypervisor_present": False}
    try:
        cpu_check = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled"],
            capture_output=True, text=True, timeout=15,
        )
        result["cpu_virtualization_enabled"] = cpu_check.stdout.strip().lower() == "true"
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        hypervisor_check = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_ComputerSystem).HypervisorPresent"],
            capture_output=True, text=True, timeout=15,
        )
        result["hypervisor_present"] = hypervisor_check.stdout.strip().lower() == "true"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def enable_hypervisor_platform() -> None:
    """Turns on Windows' "Windows Hypervisor Platform" optional feature --
    what the Android Emulator actually needs on Windows (a lighter-weight
    ask than enabling full Hyper-V, and compatible with more third-party
    virtualization software). Requires admin rights: shells out through
    a UAC elevation prompt (Windows' own consent dialog, not a silent
    escalation) rather than assuming this process is already elevated.
    Takes effect only after a restart -- this never reboots the PC
    itself, since that's a genuinely disruptive action only the person
    at the keyboard should decide when to do."""
    subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "Start-Process powershell -Verb RunAs -ArgumentList "
            "'-NoProfile -Command \"Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform -All -NoRestart\"'",
        ],
        check=True,
    )


class VirtualizationError(RuntimeError):
    """Raised instead of a plain RuntimeError specifically when the AVD
    boot failure looks like a virtualization problem (the emulator
    process either crashed almost immediately, or ran the full timeout
    with Windows' Hypervisor Platform feature confirmed off) -- callers
    with a GUI (setup_gui.py) can catch this type specifically to offer
    enable_hypervisor_platform() as an action, rather than just showing
    the message as inert text."""


def is_avd_connected() -> bool:
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    return any(line.startswith("emulator-") and "device" in line for line in result.stdout.splitlines())


def boot_avd_and_install(emulator_exe: Path, avd_name: str, env: dict, patched_apk: Path) -> None:
    process = None
    log_path = WORK_DIR / "first_boot_emulator.log"
    if is_avd_connected():
        # Resuming after an earlier failed attempt at this exact step: the
        # emulator runs fully detached, so a previous run raising an
        # exception here (e.g. the install itself failing) leaves it
        # orphaned and still running rather than cleaning up after itself.
        # Reuse it instead of launching a second instance against the same
        # AVD, which would conflict.
        print("[setup] an AVD instance is already up from an earlier attempt -- reusing it...")
    else:
        print("[setup] booting the AVD once to install iiSU (this can take a minute)...")
        import portable_sdk
        portable_sdk.disable_quickboot_autosave(portable_sdk.PORTABLE_AVD_HOME / f"{avd_name}.avd")
        log_file = open(log_path, "wb")
        try:
            process = subprocess.Popen(
                # -no-window: nothing here needs the user to see or touch
                # this boot -- it only installs the patched APK and the
                # redirector stubs over adb, then shuts back down. The
                # emulator still runs and responds to adb identically
                # headless; only the visible window is skipped.
                [str(emulator_exe), "-avd", avd_name, "-no-snapshot", "-no-window"],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                env=env,
            )
        finally:
            log_file.close()

    if not wait_for_avd(avd_name, AVD_BOOT_TIMEOUT, process=process):
        crashed_early = process is not None and process.poll() is not None
        if crashed_early:
            print(f"[setup] the emulator process exited on its own (code {process.returncode}) instead of booting.")
        else:
            print(f"[setup] the AVD did not come up within {AVD_BOOT_TIMEOUT}s on its first boot.")
        if log_path.is_file():
            print(f"[setup] last lines of {log_path}:")
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]:
                print(f"    {line}")
        else:
            print(
                "[setup] no log file to show -- an AVD instance from an earlier attempt is still "
                "running but never finished booting either. A slow, non-hardware-accelerated boot "
                "(no Hyper-V/WHPX, or virtualization disabled in BIOS) is the most common cause."
            )

        diag = virtualization_diagnostics()
        if crashed_early and not diag["cpu_virtualization_enabled"]:
            raise VirtualizationError(
                "The emulator crashed immediately instead of booting, and this PC's CPU virtualization "
                "(VT-x/AMD-V -- what Task Manager's Performance tab calls \"Virtualization\") is reported "
                "as disabled. This has to be turned on in your BIOS/UEFI first -- Windows itself can't "
                "enable it. Re-running Setup.bat resumes from here once it's on."
            )
        if crashed_early and not diag["hypervisor_present"]:
            raise VirtualizationError(
                "The emulator crashed immediately instead of booting. Your CPU has virtualization enabled, "
                "but no hypervisor is currently active on this PC -- the Android Emulator needs one "
                "(Windows Hypervisor Platform, Hyper-V, or a WHPX-compatible equivalent) actually running "
                "on top of that. Enable Windows Hypervisor Platform below, or install Google's Android "
                "Emulator Hypervisor Driver instead if you'd rather not (search for it in Android Studio's "
                "SDK Manager, or google \"Android Emulator Hypervisor Driver\") -- either one fixes this. "
                "Re-running Setup.bat resumes from here."
            )
        if crashed_early:
            raise VirtualizationError(
                f"The emulator crashed immediately (code {process.returncode}) instead of booting, even "
                "though this PC reports both CPU virtualization enabled and a hypervisor already active -- "
                f"see {log_path.name} above for the actual error (a conflict with another virtualization "
                "product, like an older VirtualBox/VMware version, is a common cause here). Re-running "
                "Setup.bat resumes from here."
            )
        raise RuntimeError(
            f"The AVD did not come up within {AVD_BOOT_TIMEOUT}s on its first boot. If your PC doesn't "
            "have hardware virtualization enabled (Hyper-V/Windows Hypervisor Platform on Windows, or "
            "virtualization enabled in your BIOS/UEFI), the emulator falls back to pure software "
            "rendering and can take several minutes instead of under a minute -- re-running Setup.bat "
            "resumes from here rather than starting over."
        )

    print("[setup] installing the patched iiSU...")
    result = None
    for attempt in range(5):
        result = subprocess.run(["adb", "install", "-r", str(patched_apk)], capture_output=True, text=True)
        if "Can't find service: package" not in result.stdout and "Can't find service: package" not in result.stderr:
            break
        # sys.boot_completed=1 (checked above) still isn't an ironclad
        # guarantee PackageManagerService itself has finished registering
        # with the service manager, which can still happen on a fresh
        # AVD's very first cold boot. A few seconds' grace clears it.
        print(f"[setup] package service not ready yet, retrying ({attempt + 1}/5)...")
        time.sleep(5)

    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in result.stdout or "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in result.stderr:
        # The AVD already has a copy of iiSU installed signed with a
        # different key than this run's -- e.g. a previous setup attempt
        # used a different keystore, or this AVD was reused from an
        # unrelated earlier install. Safe to just replace it: at this
        # point in first-time setup there's no bridge-managed app state on
        # it worth preserving.
        print("[setup] a differently-signed iiSU is already on this AVD -- removing it and reinstalling fresh...")
        subprocess.run(["adb", "uninstall", "com.iisulauncher"], capture_output=True, text=True)
        result = subprocess.run(["adb", "install", str(patched_apk)], capture_output=True, text=True)
    if result.returncode != 0 or "Success" not in result.stdout:
        raise RuntimeError(f"adb install failed:\n{result.stdout}\n{result.stderr}")

    install_default_redirectors()

    print("[setup] shutting the AVD back down (iiSU-PC.bat will bring it up properly from here on)...")
    subprocess.run(["adb", "emu", "kill"], capture_output=True, text=True)
    deadline = time.time() + 15
    while time.time() < deadline and is_avd_connected():
        time.sleep(1)
    if process is not None and process.poll() is None:
        process.terminate()


def cleanup_installer_sdk() -> None:
    """Once bridge/portable_sdk.py has its own copy of the emulator,
    platform-tools, and system image, installer/android-sdk/ (~3.7GB) and
    the downloaded commandlinetools.zip (~156MB) are pure dead weight --
    only build-tools (zipalign/apksigner) from it was ever needed post-copy,
    and only for the one-time patch step above. Deleting them trades away
    re-running Setup.bat for a *different* APK later without a fresh
    multi-GB SDK re-download -- worth it for a one-time setup tool."""
    reclaimed = 0
    for path in (sdk_bootstrap.SDK_ROOT, INSTALLER_DIR / "commandlinetools.zip"):
        if not path.exists():
            continue
        if path.is_dir():
            reclaimed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            shutil.rmtree(path)
        else:
            reclaimed += path.stat().st_size
            path.unlink()
    if reclaimed:
        print(f"[setup] freed {reclaimed / 1e9:.1f} GB by removing the now-redundant installer-side SDK copy")


def write_bridge_config(avd_name: str) -> None:
    """Creates bridge/config.json from the generic template if this is a
    fresh install (no config yet), or just patches avd_name/display into
    whatever's already there -- so re-running this against an existing,
    already-personalized setup (e.g. to rebuild a corrupted AVD) never
    overwrites someone's real roms_dir/search_roots/emulators.

    The "emulators" map is seeded from shared/emulator_defaults.py the
    same way, via setdefault: a brand-new config gets the full curated
    set, but re-running this never overwrites emulator mappings someone
    has since customized in manager.py's Emulators page."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from shared.emulator_defaults import build_emulators_map

    config_path = BRIDGE_DIR / "config.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = json.loads((INSTALLER_DIR / "config.template.json").read_text(encoding="utf-8"))
    config["avd_name"] = avd_name
    config.setdefault("display", DEFAULT_DISPLAY)
    config.setdefault("emulators", build_emulators_map())
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def install_default_redirectors() -> None:
    """Installs a stub for every package shared/emulator_defaults.py maps
    a PC emulator to (see that module for why a stub is needed at all).
    Runs while the AVD is already up from installing iiSU, right before
    it gets shut back down -- one boot instead of a second one just for
    this. Each package is independent: one failing (or already having a
    real, differently-signed app installed under that name, which this
    deliberately does not overwrite -- see stub_apk.install_stub_apk)
    doesn't stop the rest, since none of them are required for the
    install as a whole to have succeeded.

    Missing build-tools is different: that fails every single stub, not
    just one, and most consoles won't show up as playable in iiSU at all
    without their stub -- so this fails setup loudly instead of quietly
    finishing with zero consoles usable and no obvious sign why (confirmed
    live: a silent skip here is easy to miss in a long setup log)."""
    sys.path.insert(0, str(PROJECT_ROOT))
    import stub_apk
    from shared.emulator_defaults import all_stub_packages

    stub_apk.preserve_build_tools(sdk_bootstrap.SDK_ROOT)
    if not stub_apk.build_tools_available():
        expected_source = sdk_bootstrap.SDK_ROOT / "build-tools" / sdk_bootstrap.BUILD_TOOLS_VERSION
        raise RuntimeError(
            f"build-tools not found under {stub_apk.BUILD_TOOLS_DIR} (tried to copy them from "
            f"{expected_source}, which {'exists' if expected_source.is_dir() else 'does not exist'}) -- "
            "redirector apps can't be built without them, and most consoles won't appear as playable "
            "in iiSU without their stub. Re-run Setup.bat; if this keeps happening, check that the SDK "
            "download actually included build-tools."
        )

    print("[setup] installing default redirector apps (so iiSU recognizes each console's emulator)...")
    for package, label in all_stub_packages():
        try:
            outcome = stub_apk.build_and_install(package, label)
            if outcome == "conflict":
                print(f"[setup]   {label}: a different app is already installed as {package} -- left it alone")
            else:
                print(f"[setup]   {label}: {outcome}")
        except Exception as e:
            print(f"[setup]   {label}: failed ({e})")


def create_desktop_shortcut(apk_path: Path) -> None:
    """A shortcut straight to iiSU is worth having by default -- not
    something worth failing setup over if it doesn't work, so any problem
    here is reported and swallowed rather than raised.

    Passes the actual source APK through explicitly rather than letting
    create_shortcut.py re-discover one under installer/input/ -- picking
    an APK from anywhere else via Browse (setup_gui.py) works fine for
    patching, which only ever reads from wherever apk_path points, but
    icon extraction used to silently fall back to the generic icon
    whenever that wasn't also a copy sitting in installer/input/."""
    sys.path.insert(0, str(BRIDGE_DIR))
    import create_shortcut
    try:
        shortcut_path = create_shortcut.create_desktop_shortcut(apk_path)
        print(f"[setup] created a desktop shortcut: {shortcut_path}")
    except Exception as e:
        print(f"[setup] couldn't create a desktop shortcut ({e}) -- you can still use iiSU-PC.bat directly")


def run_setup(apk_path: Path, on_stage: Callable[[str, int, int], None] | None = None) -> None:
    """Does the actual work, given a source APK path -- shared by the CLI
    entry point below and setup_gui.py, so both stay in sync with exactly
    one implementation. Reports progress via plain print(), which the GUI
    captures by redirecting sys.stdout for the duration of the call.

    on_stage (if given) is called at the start of each of SETUP_STAGES, so
    a GUI can show "Step 3/7: ..." somewhere more durable than a scrolling
    log -- the whole run used to be one indeterminate spinner from start to
    finish, which gives no sense of whether a several-minute step is normal
    progress or actually stuck."""
    total = len(SETUP_STAGES)

    def stage(index: int) -> None:
        label = SETUP_STAGES[index]
        print(f"\n=== Step {index + 1}/{total}: {label} ===")
        if on_stage:
            on_stage(label, index + 1, total)

    print("=== iiSU-PC first-time setup ===\n")
    stage(0)
    require_java()
    ensure_pillow()
    print(f"[setup] using {apk_path.name} as the source APK")
    validate_iisu_apk(apk_path)
    check_disk_space()

    WORK_DIR.mkdir(parents=True, exist_ok=True)

    stage(1)
    avd_dir = sdk_bootstrap.ensure_sdk_and_avd(DEFAULT_AVD_NAME)
    print(f"[setup] AVD ready: {avd_dir}")

    stage(2)
    keystore, keystore_password = ensure_keystore()

    stage(3)
    patched_apk = WORK_DIR / "iisu-patched.apk"
    patch_apk(
        source_apk=apk_path,
        output_apk=patched_apk,
        work_dir=WORK_DIR / "patch",
        zipalign_exe=sdk_bootstrap.zipalign_exe(),
        apksigner_exe=sdk_bootstrap.apksigner_bat(),
        keystore=keystore,
        keystore_pass=keystore_password,
        key_alias=KEY_ALIAS,
    )

    sys.path.insert(0, str(BRIDGE_DIR))
    import portable_sdk

    stage(4)
    env_overrides = portable_sdk.ensure_portable_sdk(DEFAULT_AVD_NAME, sdk_bootstrap.SDK_ROOT)

    import os
    env = os.environ.copy()
    env.update(env_overrides)
    emulator_exe = portable_sdk.PORTABLE_SDK / "emulator" / "emulator.exe"

    stage(5)
    boot_avd_and_install(emulator_exe, DEFAULT_AVD_NAME, env, patched_apk)

    stage(6)
    write_bridge_config(DEFAULT_AVD_NAME)
    cleanup_installer_sdk()
    create_desktop_shortcut(apk_path)

    print("\n=== Setup complete ===")
    print(f"iiSU is installed and the bridge is configured for AVD '{DEFAULT_AVD_NAME}'.")
    print("Run 'iiSU-PC.bat' (one folder up) to configure and launch.")


def main() -> None:
    apk_path = find_input_apk()
    if apk_path is None:
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        print(f"No iiSU APK found in {INPUT_DIR}.")
        print("Drop your own copy of the iiSU APK into that folder (any filename, .apk extension)")
        print("and run this again. This tool patches your copy -- it doesn't come with one.")
        sys.exit(1)
    run_setup(apk_path)


if __name__ == "__main__":
    main()
