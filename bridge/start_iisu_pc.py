"""
One-shot launcher for the whole iiSU-PC setup: checks for updates to the
project itself (updater.py), then starts the AVD if it isn't already
running, then starts the launch bridge if it isn't already running.
Covers a cold boot with a fullscreen black overlay (boot_overlay.py) the
whole time, since the AVD's window isn't made fullscreen until well into
this sequence and would otherwise leave raw desktop visible around it.

This is the single entry point meant for day-to-day use -- it's what runs
when you click Open (or Stop -> Open again) on manager.py's Home page,
instead of manually starting the emulator and the bridge as separate
steps.

Note: this intentionally does NOT use the `android emulator start` wrapper.
That command promises to "return when the emulator is fully started," but
hangs forever even after the AVD is fully booted and usable: it launches
emulator.exe as a grandchild and captures its output via a pipe, and
emulator.exe (the long-lived VM process) inherits that pipe's write end, so
the pipe's read side never sees EOF -- subprocess.run() then blocks forever
waiting for output that will never stop. Launching emulator.exe directly,
detached and with output discarded (not piped), avoids the whole class of
problem, and we poll `adb devices` ourselves to know when it's ready.

It also always launches from the portable SDK/AVD copy under
android-sdk-portable/ (see portable_sdk.py) rather than the system-wide
Android Studio install -- see portable_sdk.py for why.

Always launches with -no-snapshot: a quickboot-resumed AVD carries its
mount/storage state forward from whenever the snapshot was captured,
which can go stale in ways a fresh boot doesn't hit (e.g. the emulated
SD card failing to (re)mount correctly). A real cold boot costs maybe
30-60s more; that's cheap insurance against a whole class of "why is my
storage broken" bug reports compared to a snapshot resume that's a few
seconds faster but occasionally wrong.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import boot_overlay
import sync_library
import updater
from bridge_config import ConfigMissingError, load_config
from launch_bridge import launch_iisu, show_iisu_window
from portable_sdk import PORTABLE_AVD_HOME, PORTABLE_SDK, disable_quickboot_autosave, ensure_portable_sdk

BRIDGE_SCRIPT = Path(__file__).parent / "launch_bridge.py"
STATE_PATH = Path(__file__).parent / ".runtime_state.json"
EMULATOR_LOG_PATH = Path(__file__).parent / "emulator.log"
BRIDGE_LOG_PATH = Path(__file__).parent / "bridge.log"

# Generous on purpose: without hardware virtualization (Hyper-V/WHPX on
# Windows, or disabled in the BIOS/UEFI) the emulator falls back to pure
# software rendering, which can take several minutes even for a normal
# (already-set-up) boot -- see the matching constant in setup_wizard.py.
AVD_BOOT_TIMEOUT = 300  # seconds
MAX_LAUNCH_ATTEMPTS = 3
RETRY_DELAY = 5  # seconds

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f)


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def is_avd_running(avd_name: str) -> bool:
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    # A running AVD shows up as "emulator-5554\tdevice" (or similar) once booted.
    return any(line.startswith("emulator-") and "device" in line for line in result.stdout.splitlines())


def find_system_emulator_exe() -> Path | None:
    """Locates the existing Android Studio SDK install, used only as a
    one-time copy source for bootstrapping the portable copy -- actual
    launches always use the portable copy, never this."""
    candidates = [
        Path(os.environ.get("ANDROID_SDK_ROOT", "")) / "emulator" / "emulator.exe",
        Path(os.environ.get("ANDROID_HOME", "")) / "emulator" / "emulator.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "emulator" / "emulator.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def clear_stale_locks(avd_dir: Path) -> None:
    """emulator.exe refuses to start (exits immediately, code 1) if it finds
    lock files from a previous instance that didn't shut down cleanly --
    e.g. after a crash, a forced Task Manager kill, or closing the emulator
    window without going through adb. A clean `adb emu kill` releases these
    itself, but we can't guarantee every past shutdown was clean, so this
    clears them defensively before every launch."""
    if not avd_dir.is_dir():
        return
    for lock_path in avd_dir.glob("*.lock"):
        if lock_path.is_dir():
            for child in lock_path.iterdir():
                child.unlink(missing_ok=True)
            lock_path.rmdir()
        else:
            lock_path.unlink(missing_ok=True)


def diagnose_system_image(avd_dir: Path, sdk_root: str) -> None:
    """Prints hard evidence about the exact files emulator.exe just refused
    to accept, since its own "not a valid directory" / "Broken AVD system
    path" errors give no detail."""
    config_ini = avd_dir / "config.ini"
    if not config_ini.is_file():
        print(f"[start] diagnostic: {config_ini} not found")
        return
    sysdir = None
    for line in config_ini.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("image.sysdir.1="):
            sysdir = line.split("=", 1)[1].strip()
            break
    if sysdir is None:
        print("[start] diagnostic: image.sysdir.1 not found in config.ini")
        return
    image_dir = Path(sdk_root) / sysdir
    print(f"[start] diagnostic: checking {image_dir}")
    if not image_dir.is_dir():
        print("[start] diagnostic: directory does not exist right now")
        return
    for name in ("package.xml", "system.img", "build.prop"):
        path = image_dir / name
        if not path.is_file():
            print(f"[start] diagnostic: {name} is missing")
            continue
        try:
            with open(path, "rb") as f:
                f.read(16)
            print(f"[start] diagnostic: {name} ok ({path.stat().st_size} bytes, readable)")
        except OSError as e:
            print(f"[start] diagnostic: {name} exists but could not be read ({e}) -- likely locked by another process")


def build_usb_passthrough_args(usb_passthrough: list[dict]) -> list[str]:
    """Builds -usb-passthrough flags for real USB controllers (e.g. a PS5
    DualSense) so Android's own native gamepad driver (present since
    Android 12) handles them directly inside the guest -- no PC-side input
    translation needed for these, unlike Xbox-compatible controllers which
    go through controller_bridge.py's XInput polling instead. Matching by
    vendorid/productid alone (no hostbus/hostport) means it works from
    whichever USB port the controller happens to be plugged into, and is a
    no-op if the controller isn't currently connected -- confirmed via the
    emulator's own -help-usb-passthrough documentation."""
    args = []
    for device in usb_passthrough:
        args += ["-usb-passthrough", f"vendorid={device['vendorid']},productid={device['productid']}"]
    return args


def _launch_once(emulator_exe: Path, avd_name: str, env: dict, usb_passthrough: list[dict], debug_console: bool) -> int | None:
    """One launch attempt. Logged to a real file, not a pipe, by default: a
    pipe's write end would get inherited by this long-lived process and
    never see EOF (the same bug that made `android emulator start` hang),
    but a file has no such problem and still lets us show the real error
    on failure.

    debug_console (config.json's "debug_show_console_windows") swaps that
    for a real, visible console instead -- for watching emulator.exe's
    (and whatever it spawns internally, e.g. netsimd) own live output
    while troubleshooting something a static log doesn't make obvious.
    Trades away emulator.log for that run, since a process can't
    sensibly have both a console showing its output live and that same
    output redirected to a file."""
    if debug_console:
        process = subprocess.Popen(
            [str(emulator_exe), "-avd", avd_name, "-no-snapshot", *build_usb_passthrough_args(usb_passthrough)],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env=env,
        )
    else:
        log_file = open(EMULATOR_LOG_PATH, "wb")
        try:
            process = subprocess.Popen(
                [str(emulator_exe), "-avd", avd_name, "-no-snapshot", *build_usb_passthrough_args(usb_passthrough)],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                env=env,
            )
        finally:
            log_file.close()

    deadline = time.time() + AVD_BOOT_TIMEOUT
    while time.time() < deadline:
        if is_avd_running(avd_name):
            return process.pid
        if process.poll() is not None:
            print(f"[start] emulator.exe exited early (code {process.returncode}) before the AVD came up.")
            if debug_console:
                print("[start] its console window has the output (nothing was logged to a file this run).")
            else:
                print(f"[start] see {EMULATOR_LOG_PATH} for details. Last lines:")
                tail = EMULATOR_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
                for line in tail:
                    print(f"    {line}")
            return None
        time.sleep(2)
    print(
        f"[start] {avd_name} did not report ready within {AVD_BOOT_TIMEOUT}s. If your PC doesn't have "
        "hardware virtualization enabled (Hyper-V/Windows Hypervisor Platform, or in your BIOS/UEFI), "
        "the emulator falls back to pure software rendering and can take several minutes."
    )
    return None


def start_avd(avd_name: str, usb_passthrough: list[dict], debug_console: bool = False) -> int | None:
    """Launches the AVD directly (bypassing the buggy `android emulator
    start` wrapper) and returns its PID once it's confirmed running, so the
    stop script can find it reliably even if it later gets reparented.

    Always launches against the portable SDK/AVD copy under
    android-sdk-portable/, bootstrapping it from the system-wide Android
    Studio install on first run if it doesn't exist yet -- the system-wide
    install under %LOCALAPPDATA%\\Android\\Sdk has been unreliable, failing
    intermittently with "Broken AVD system path" even with the files
    verified present and ANDROID_SDK_ROOT passed explicitly. The portable
    copy lives in a plain folder next to this script instead."""
    system_emulator_exe = find_system_emulator_exe()
    if system_emulator_exe is None and not (PORTABLE_SDK / "emulator" / "emulator.exe").is_file():
        print("[start] Could not find an existing Android Studio emulator install to bootstrap the portable copy from.")
        return None

    try:
        env_overrides = ensure_portable_sdk(
            avd_name, system_emulator_exe.parent.parent if system_emulator_exe else None
        )
    except RuntimeError as e:
        print(f"[start] Failed to set up the portable SDK/AVD copy: {e}")
        return None

    emulator_exe = PORTABLE_SDK / "emulator" / "emulator.exe"
    avd_dir = PORTABLE_AVD_HOME / f"{avd_name}.avd"
    env = os.environ.copy()
    env.update(env_overrides)

    for attempt in range(1, MAX_LAUNCH_ATTEMPTS + 1):
        clear_stale_locks(avd_dir)
        disable_quickboot_autosave(avd_dir)
        pid = _launch_once(emulator_exe, avd_name, env, usb_passthrough, debug_console)
        if pid is not None:
            return pid
        diagnose_system_image(avd_dir, env_overrides["ANDROID_SDK_ROOT"])
        if attempt < MAX_LAUNCH_ATTEMPTS:
            print(f"[start] attempt {attempt}/{MAX_LAUNCH_ATTEMPTS} failed, retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
    return None


def sync_rom_library() -> None:
    """Keeps the AVD's placeholder ROM tree in sync with the real library
    on every start, rather than requiring a separate manual sync_library.py
    run -- without this, /sdcard/Roms never gets created at all, so iiSU
    has nothing to scan no matter how the ROM directory is configured. A
    sync failure (e.g. roms_dir temporarily unreachable on a network
    share) shouldn't block starting the bridge, so this only reports it."""
    try:
        sync_library.main()
    except SystemExit as e:
        if e.code not in (0, None):
            print("[start] ROM library sync reported a problem (see above) -- continuing anyway")
    except Exception as e:  # noqa: BLE001 -- reported, not fatal to starting up
        print(f"[start] ROM library sync failed ({e}) -- continuing anyway")


def main() -> None:
    updater.check_for_updates()

    try:
        config = load_config()
    except ConfigMissingError as e:
        print(f"[start] {e}")
        sys.exit(1)
    avd_name = config["avd_name"]
    port = config["bridge_port"]
    debug_console = config.get("debug_show_console_windows", False)
    state = {"avd_name": avd_name}

    # A cold-booting AVD leaves raw desktop visible around its window
    # until launch_bridge.py's show_iisu_window() makes it fullscreen,
    # further down this same sequence -- covered with a fullscreen
    # overlay for the whole stretch instead. Skipped when the AVD is
    # already up (iiSU is presumably already on screen normally, nothing
    # to cover) and when debug_console is on (it would just hide the
    # console windows that setting exists to show).
    show_overlay = not is_avd_running(avd_name) and not debug_console
    overlay = boot_overlay.show("Booting iiSU-PC, please wait...") if show_overlay else None
    try:
        _run_start_sequence(config, avd_name, port, debug_console, state)
    finally:
        boot_overlay.close(overlay)


def _run_start_sequence(config: dict, avd_name: str, port: int, debug_console: bool, state: dict) -> None:
    if is_avd_running(avd_name):
        print(f"[start] {avd_name} is already running.")
    else:
        print(f"[start] Starting {avd_name}, this can take a minute...")
        pid = start_avd(avd_name, config.get("usb_passthrough", []), debug_console)
        if pid is None:
            sys.exit(1)
        state["emulator_pid"] = pid
        print(f"[start] {avd_name} is up.")

    print("[start] Syncing your ROM library into the AVD...")
    sync_rom_library()

    if is_port_open(port):
        # The bridge being up already doesn't mean iiSU itself is in the
        # state a fresh launch would leave it in -- it could be minimized,
        # showing the Android home screen instead of iiSU (backed out at
        # some point), or just not fullscreen. A brand-new bridge process
        # always re-launches iiSU and re-applies fullscreen as part of its
        # own startup (see launch_bridge.main()); doing nothing here in the
        # "already running" case meant relaunching iiSU-PC while a bridge
        # was already alive -- whether genuinely left running on purpose,
        # or a stale one Stop failed to clean up -- was a silent no-op:
        # exactly the "shortcut sometimes only opens the emulator, not
        # iiSU, or not fullscreen" symptom, since the shortcut gives no
        # visible sign anything happened at all when nothing did.
        print(f"[start] Bridge is already running on port {port} -- bringing iiSU to the foreground...")
        launch_iisu(config)
        if config.get("iisu_fullscreen"):
            show_iisu_window(config)
    else:
        if debug_console:
            print("[start] Starting the launch bridge in a visible console (debug_show_console_windows is on)...")
            bridge_process = subprocess.Popen(
                [sys.executable, str(BRIDGE_SCRIPT)],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=str(BRIDGE_SCRIPT.parent),
            )
        else:
            print(f"[start] Starting the launch bridge (logging to {BRIDGE_LOG_PATH.name})...")
            bridge_log_file = open(BRIDGE_LOG_PATH, "wb")
            try:
                bridge_process = subprocess.Popen(
                    [sys.executable, str(BRIDGE_SCRIPT)],
                    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                    stdin=subprocess.DEVNULL,
                    stdout=bridge_log_file,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    cwd=str(BRIDGE_SCRIPT.parent),
                )
            finally:
                bridge_log_file.close()
        state["bridge_pid"] = bridge_process.pid
        # Give it a moment to bind before reporting success. Generous on
        # purpose: launch_iisu() inside the bridge retries `am start` for up
        # to a minute on its own (package manager can take a while to be
        # ready right after a cold boot -- see its docstring), and the
        # socket doesn't open until after that succeeds.
        for _ in range(120):
            if is_port_open(port):
                break
            time.sleep(0.5)
        if is_port_open(port):
            print("[start] Bridge is up.")
        elif debug_console:
            print("[start] Bridge didn't come up in time -- check its console window for errors.")
        else:
            print(f"[start] Bridge didn't come up in time -- check {BRIDGE_LOG_PATH.name} for errors.")

    save_state(state)
    print("[start] Ready. Launching a game in iiSU will now hand off to the real PC emulator.")


if __name__ == "__main__":
    main()
