"""
iiSU launch bridge.

Listens on the AVD's host-loopback address (10.0.2.2 from inside the emulator
== this machine, on the configured port) for the raw Intent dump sent by the
patched com.iisulauncher.pcbridge.LaunchBridge class. Each connection carries
exactly one launch request; the socket is closed after the payload is sent.

For each request, this:
  1. Parses the component package name and ROM URI out of the Intent dump
     (ROMs travel via ClipData, sent separately as CLIPURI: lines, since
     Intent.toString() only shows a truncated placeholder for ClipData).
  2. Matches the package name against config.json's "emulators" map to find
     a PC emulator (name + fullscreen flag) and searches "search_roots" for
     its executable.
  3. Takes the ROM filename from the end of the URI and looks it up under
     "roms_dir" by matching filename (the patched app can only tell us what
     it knows about its own Android-side content URI, not a Windows path,
     so both sides need to agree on ROM filenames living in roms_dir).
  4. Covers the screen with a fullscreen overlay (boot_overlay.py),
     minimizes the iiSU/AVD window, launches the matching emulator in
     fullscreen, forces it to the foreground, synthesizes a click so
     keyboard/controller input is picked up immediately (Qt apps track
     actual input focus on their render widget, separately from the OS-level
     foreground window), then drops the overlay, without it, the moment
     between iiSU minimizing and the emulator's window taking over would
     show raw desktop.
  5. Waits for the emulator to exit (either normally, or forced via the
     configured quit_hotkey) and restores the iiSU window (maximized if
     "iisu_fullscreen" is set), mirroring how the real Android launcher
     reappears once a game exits.

All configuration (ROM directory, emulator search paths, package->emulator
mappings, quit hotkey, display settings) lives in config.json next to this
script. Window-management helpers live in winapi.py, shared with
apply_display.py and the Manager. One Python stdlib script, no dependencies.
"""

import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import threading
import traceback
import time
from ctypes import wintypes
from pathlib import Path
from urllib.parse import unquote

# The embeddable Python runtime this app ships with (installer/
# build_embedded_python.py) does NOT auto-add a launched script's own
# directory to sys.path the way a normal Python install does, confirmed
# live: this exact bare import failed with ModuleNotFoundError on a real
# installed copy, since start_iisu_pc.py launches this file as a raw
# script path (subprocess.Popen([sys.executable, str(BRIDGE_SCRIPT)]),
# not "-m"), and a system Python's implicit sys.path[0] masked the gap
# during development against a git checkout. Explicit, not implicit, for
# every bare import below.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# The project root too: portable_sdk and the rest import the shared/ package, which
# sits next to bridge/, not inside it, and a raw script launch only gets its own folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import game_overrides
import portable_sdk  # noqa: F401; imported for its import-time PATH fix (adb), not used directly here
from bridge_config import ConfigMissingError, load_config
from controller_bridge import ControllerBridge
from emulator_lookup import (
    build_pc_launch_args,
    core_dll_from_pre_args,
    ensure_retroarch_core,
    find_emulator_for_package,
    find_executable,
    find_rom,
    friendly_emulator_name,
    load_path_cache,
    save_path_cache,
)
from hotkey_keys import MODIFIER_FLAGS, MODIFIER_VK, resolve_vk

# Needed to reach bridge.ui.boot_overlay_qt below regardless of this
# script's own cwd, start_iisu_pc.py always launches this as its own
# subprocess with cwd set to this directory, not the project root.
sys.path.insert(0, str(Path(__file__).parent.parent))
from bridge.ui import boot_overlay_qt as boot_overlay
from shared import adb_client, app_version
from shared.platform_compat import IS_WINDOWS, detached_popen_kwargs, new_console_creationflags, open_uri, subprocess_creationflags
from shared.vm_backend import BACKEND_WAYDROID, resolve_backend, vm_device_connected
from winapi import (
    SW_MINIMIZE,
    SW_RESTORE,
    find_window_by_title,
    force_foreground,
    hide_emulator_toolbar,
    list_visible_windows,
    close_window,
    make_fullscreen,
    nudge_focus_with_click,
    user32,
    wait_for_new_visible_window,
    wait_for_new_visible_window_excluding_processes,
    wait_for_window_by_pid,
    wait_for_visible_window_by_pid,
    wait_for_window_to_close,
)

STOP_SCRIPT = Path(__file__).parent / "stop_iisu_pc.py"
STOP_LOG_PATH = Path(__file__).parent / "stop.log"
LAUNCH_LOG_PATH = Path(__file__).parent / "launch_history.log"
WINDOWS_APPS_PATH = Path(__file__).parent / "windows_apps.json"

DEFAULT_IISU_COMPONENT = "com.iisulauncher/com.iisulauncher.launcher.StartupSafeModeActivity"

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

WM_HOTKEY = 0x0312
SHUTDOWN_HOTKEY_ID = 2
MOD_NOREPEAT = 0x4000

KEY_POLL_INTERVAL = 0.03  # seconds, responsive without busy-looping
BOOTANIM_WAIT_SECONDS = 90  # generous cap; see launch_iisu's docstring


def _is_key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)

# Loopback only: the AVD reaches this over its host-loopback alias
# (10.0.2.2), which is routed to the host's own 127.0.0.1 regardless of
# which local address this actually binds, there's no reason for this to
# be reachable from the LAN. The protocol has no authentication at all
# (anything that can open the socket can make this launch an arbitrary
# configured emulator, or hand an arbitrary app_id to Steam's URI handler),
# so binding 0.0.0.0 would expose the bridge unnecessarily.
HOST = "127.0.0.1"

INTENT_CMP_RE = re.compile(r"cmp=(\S+)")
INTENT_DAT_RE = re.compile(r"dat=(\S+)")

# iiSU's own default emulator list routes Steam/GOG/Epic-style entries to
# GameNative, an Android app that runs Windows PC games under Wine/Box64,
# irrelevant here, since this project always has the real thing (Steam
# itself) available PC-side already. Its launch command
# (emuladores_default.json's "%PACKAGE%/.MainActivity -a
# app.gamenative.LAUNCH_GAME -e app_id %GAMENATIVE_APP_ID_INT%") passes the
# Steam App ID as a plain int Intent extra, not a file path or ClipData URI,
# there's no "rom" to find under roms_dir at all, unlike every other
# entry in config.json's emulators map, so this is handled as its own
# special case below rather than forced into the exe_names/rom_path shape
# every other profile uses.
GAMENATIVE_PACKAGE = "app.gamenative"
WINDOWS_LAUNCH_PACKAGES = {"com.winlator.cmod", "com.winlator", "com.cmodded.winlator"}

# The one byte answered to the patched iiSU after each launch (see
# installer/smali_patch/LaunchBridge$1.smali). "A" hands the launch back to
# Android unchanged; "P" means the PC took it.
REPLY_LAUNCH_ON_ANDROID = b"A"
REPLY_HANDLED_ON_PC = b"P"
# The line a patched iiSU adds to every launch once it reads that answer. An
# iiSU patched before then hangs up without reading it, so for that one the
# bridge starts the Android app itself, and nobody has to re-patch.
REPLY_CAPABLE_LINE = "BRIDGE_REPLY:1"
INTENT_ACT_RE = re.compile(r"\bact=(\S+)")
INTENT_CAT_RE = re.compile(r"\bcat=\[([^\]]*)\]")
INTENT_FLG_RE = re.compile(r"\bflg=(0x[0-9a-fA-F]+)")

# Set to the currently-running emulator Popen while a game is active, so the
# quit hotkey listener (on its own thread) has something to terminate.
current_process: subprocess.Popen | None = None
# Native/URI launches may not have a useful Popen handle (Steam is the classic
# case), so track the actual game HWND separately for quit-hotkey handling.
current_native_window: int | None = None
# Keep the owning PID too. Some Steam games destroy/recreate their top-level
# HWND while the process itself keeps running; the PID is the stable fallback
# for controller gating and the quit hotkey.
current_native_pid: int | None = None
# Becomes True immediately when iiSU hands a launch to the PC side. This
# closes the Steam startup gap before a real native HWND/PID exists.
game_handoff_active = False
# Android media-stream volume captured when a PC game starts, so iiSU's
# frontend music can be muted during play and restored afterward.
saved_android_media_volume: int | None = None
# If the quit hotkey is tapped after a native launch starts but before its
# real HWND/PID has been discovered, remember the request instead of dropping
# it. The game is closed as soon as tracking becomes available.
pending_native_quit = False
current_process_lock = threading.Lock()

DEBUG_LOG_PATH = Path(__file__).resolve().parent / "bridge_debug.log"
_debug_log_lock = threading.Lock()


def debug_log(message: str) -> None:
    """Append a timestamped diagnostic line to bridge_debug.log."""
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with _debug_log_lock:
            with DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(f"[{timestamp}] {message}\n")
                fh.flush()
    except Exception:
        # Diagnostics must never be able to crash the bridge.
        pass


def _log_uncaught_exception(exc_type, exc_value, exc_tb) -> None:
    formatted = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    debug_log("UNCAUGHT EXCEPTION\\n" + formatted.rstrip())
    # Preserve normal console traceback behavior too.
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _log_thread_exception(args) -> None:
    formatted = "".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
    )
    debug_log(
        f"UNCAUGHT THREAD EXCEPTION in {getattr(args.thread, 'name', '<unknown>')}\\n"
        + formatted.rstrip()
    )






def _window_pid(hwnd: int) -> int | None:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) if pid.value else None


def _pid_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    # tasklist is available on supported Windows installs and avoids keeping
    # another process HANDLE open for the whole game session.
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=subprocess_creationflags(),
            timeout=2,
        )
        return result.returncode == 0 and f'"{pid}"' in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _terminate_native_pid(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess_creationflags(),
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _adb_media_volume_get() -> int | None:
    """Read Android's MUSIC stream volume (stream 3) without showing a
    console. Tries the current `cmd media_session volume` API first,
    falling back to the older `media volume` command some system image
    versions still need. Both run in one device shell command."""
    _status, output = adb_client.shell(
        "cmd media_session volume --stream 3 --get 2>/dev/null || media volume --stream 3 --get 2>/dev/null", timeout=5,
    )
    # Typical Android output: "volume is 7 in range [0..15]"
    match = re.search(r"volume\s+is\s+(\d+)", output, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _adb_media_volume_set(volume: int) -> bool:
    level = max(0, volume)
    status, _output = adb_client.shell(
        f"cmd media_session volume --stream 3 --set {level} >/dev/null 2>&1 "
        f"|| media volume --stream 3 --set {level} >/dev/null 2>&1",
        timeout=5,
    )
    return status == 0


def begin_game_handoff() -> None:
    """Immediately stop iiSU menu input and mute its Android media stream."""
    debug_log("begin_game_handoff() entered")
    global game_handoff_active, saved_android_media_volume, pending_native_quit
    with current_process_lock:
        # Treat every launch as a fresh handoff. A previous launch may have
        # ended through a path that left the boolean set for a few moments.
        game_handoff_active = True
        pending_native_quit = False

    # Do the volume work outside the lock; adb can take a moment to answer.
    previous = _adb_media_volume_get()
    if previous is not None:
        saved_android_media_volume = previous
        if previous != 0:
            _adb_media_volume_set(0)
    print("[bridge] iiSU input/audio suspended for PC game handoff")


# The emulator's stand-in on Android (installer/stub_apk_template). iiSU's
# launch is sent to the PC, so iiSU never leaves the screen and never gets the
# return to the foreground that restores its music and interface after a game.
# Opening the stand-in with HOLD covers iiSU the way a real Android emulator
# would; opening it again without HOLD closes it, and iiSU comes back.
STUB_ACTIVITY = "com.iisupc.stub.RedirectorActivity"
STUB_HOLD_EXTRA = "com.iisupc.stub.HOLD"
held_stub_package: str | None = None


def hold_iisu_behind_stub(package: str | None) -> None:
    """Covers iiSU with the launched package's stand-in for as long as the PC
    game runs. A package with no stand-in (a real Android app, or a stub
    installed before stand-ins held) answers with an error and is skipped."""
    global held_stub_package
    if not package:
        return
    component = adb_client.quote(f"{package}/{STUB_ACTIVITY}")
    status, output = adb_client.shell(f"am start -n {component} --ez {STUB_HOLD_EXTRA} true", timeout=10)
    if status == 0 and "Error" not in output:
        held_stub_package = package
    else:
        debug_log(f"no stand-in to hold iiSU for {package}: {output.strip()}")


def release_iisu_from_stub() -> None:
    """Closes the stand-in, so Android returns to iiSU like a game exiting."""
    global held_stub_package
    package, held_stub_package = held_stub_package, None
    if package:
        adb_client.shell(f"am start -n {adb_client.quote(f'{package}/{STUB_ACTIVITY}')}", timeout=10)


def end_game_handoff() -> None:
    """Restore iiSU controller navigation and its previous Android volume."""
    debug_log("end_game_handoff() entered")
    global game_handoff_active, saved_android_media_volume, pending_native_quit
    release_iisu_from_stub()
    previous = saved_android_media_volume
    saved_android_media_volume = None
    if previous is not None:
        _adb_media_volume_set(previous)
    with current_process_lock:
        game_handoff_active = False
        pending_native_quit = False
    print("[bridge] iiSU input/audio restored")


def log_launch(line: str, notify: bool = False, notify_title: str = "Community-iiSU-PC") -> None:
    """Appends one line to launch_history.log with a timestamp, every
    launch attempt gets logged here regardless of outcome, not just
    failures, so there's always a record to check against ("did this
    actually try to launch, and with what") rather than only ever finding
    out about a problem after the fact with nothing to look back on.
    notify additionally raises a tray balloon (see boot_overlay.
    notify_error) for anything worth interrupting someone over, a
    failure, not a routine successful launch, since the bridge runs
    with no visible window normally and a log file nobody's looking at
    doesn't "make the user aware" of anything by itself."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LAUNCH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {line}\n")
    except OSError:
        pass
    if notify:
        boot_overlay.notify_error(notify_title, line)


def _is_waydroid(config: dict) -> bool:
    return resolve_backend(config) == BACKEND_WAYDROID


def launch_iisu(config: dict) -> None:
    """Starts iiSU's own main activity directly via adb, instead of leaving
    the stock Android home screen showing after boot. iiSU declares both
    LAUNCHER and HOME categories on this activity (it's designed to be a
    home-screen replacement), but isn't necessarily set as this AVD's
    default home app, so this just launches it directly rather than
    depending on that.

    Waits for init.svc.bootanim to report "stopped" first (the boot
    animation ending, a cheap getprop check) before making any am start
    attempt at all: measured live, am start reliably fails for the whole
    stretch before that point, so trying earlier is a guaranteed-wasted
    adb round trip, not a head start. Capped at BOOTANIM_WAIT_SECONDS so a
    system image that never sets this property (or sets it oddly) still
    falls through to the retry loop below rather than hanging.

    Still does NOT gate on sys.boot_completed, unlike setup_wizard.py's
    own separate wait_for_avd() (used only for the one-time install boot,
    where waiting longer is fine): boot_completed only flips once *every*
    system app's BOOT_COMPLETED receiver has finished, including this
    project's own redirector stubs, and can lag well behind bootanim once
    more of them are installed, confirmed live on an earlier run
    (iiSU sitting on the stock home screen for 1-2 minutes with
    boot_completed still unset). Retries after that since `am start` can
    still fail with a transient "does not exist" error for a moment even
    once the system looks interactive, package manager can still be
    resolving components (confirmed via `dumpsys package`: the activity is
    genuinely registered, `am start` just tried too early).

    Both waits run on the device in one shell command (_LAUNCH_IISU_SCRIPT),
    checking every 0.2 seconds, instead of one adb command line per check
    from here: no 26 ms process start per check, and iiSU starts within a
    fifth of a second of Android accepting it rather than up to a second
    later. The device prints a marker after each stage, timed here as it
    arrives."""
    started = time.monotonic()
    component = config.get("iisu_component", DEFAULT_IISU_COMPONENT)
    # Waydroid has already waited for sys.boot_completed in
    # waydroid_backend.start() and has no boot animation to watch for.
    bootanim_wait = 0 if _is_waydroid(config) else BOOTANIM_WAIT_SECONDS
    script = _LAUNCH_IISU_SCRIPT.format(
        bootanim_wait=bootanim_wait, start_wait=IISU_START_WAIT_SECONDS, component=adb_client.quote(component),
    )
    boot_wait = None
    attempt = None
    status = None
    output = []
    for line in adb_client.shell_lines(script, timeout=bootanim_wait + IISU_START_WAIT_SECONDS + 30):
        code = adb_client.exit_status(line)
        if code is not None:
            status = code
        elif line == "bootanim_done":
            boot_wait = time.monotonic() - started
        elif line.startswith("started_attempt="):
            attempt = line.split("=", 1)[1]
        else:
            output.append(line)
    if status == 0:
        print(
            f"[bridge] iiSU started {time.monotonic() - started:.1f}s after asking "
            f"(boot animation wait {boot_wait or 0:.1f}s, am start attempt {attempt})"
        )
        set_volume_max()
        return
    print(f"[bridge] could not launch iiSU ({component}):")
    print("    " + ("\n    ".join(output[-5:]) or "no answer from adb"))


# Waits on the device for the boot animation to end (up to {bootanim_wait}s),
# then retries `am start` until Android accepts it (up to {start_wait}s),
# checking every 0.2 seconds. Prints bootanim_done, then started_attempt=N
# and exits 0 on success; exits 1 with am start's last output otherwise.
_LAUNCH_IISU_SCRIPT = (
    "end=$(( $(date +%s) + {bootanim_wait} )); "
    "while [ \"$(getprop init.svc.bootanim)\" != stopped ] && [ $(date +%s) -lt $end ]; do sleep 0.2; done; "
    "echo bootanim_done; "
    "end=$(( $(date +%s) + {start_wait} )); n=0; out=; "
    "while [ $(date +%s) -lt $end ]; do "
    "n=$((n+1)); out=$(am start -n {component} 2>&1); "
    "case \"$out\" in *Error*) sleep 0.2;; *) echo started_attempt=$n; exit 0;; esac; "
    "done; "
    "echo \"$out\"; exit 1"
)
IISU_START_WAIT_SECONDS = 60


def set_volume_max() -> None:
    """A fresh boot comes up at whatever media volume level the system
    image defaults to (usually well below max), silent enough that
    anything iiSU itself plays (UI sounds, trailers) needs a manual
    volume raise inside the VM on every single boot otherwise. Repeated
    VOLUME_UP keyevents clamp at the device's actual max regardless of
    AOSP vs OEM MAX_VOLUME differences, so this doesn't need to know the
    exact volume index, one `adb shell input keyevent` call with the
    keycode repeated is enough, no need for 20 separate subprocess calls."""
    adb_client.shell("input keyevent " + " ".join(["24"] * 20), timeout=15)


def show_iisu_window(config: dict) -> None:
    """Brings the iiSU/AVD window to the foreground, borderless-fullscreen
    if "iisu_fullscreen" is set in config.json. Plain SW_MAXIMIZE doesn't
    work on this window (it clamps its own max size), so true fullscreen
    means stripping the title bar/border and resizing to the screen, see
    winapi.make_fullscreen."""
    end_game_handoff()
    if _is_waydroid(config):
        # Waydroid draws iiSU as one fullscreen Wayland window and there is
        # no emulator toolbar or frame to strip. Wayland offers no way to
        # raise another client's window, but Waydroid's own `show-full-ui`
        # brings its window back to the front.
        subprocess.Popen(
            ["waydroid", "show-full-ui"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=subprocess_creationflags(),
        )
        return
    hwnd = find_window_by_title(config["iisu_window_title"])
    if hwnd is None:
        print("[bridge] could not locate iiSU window")
        return
    force_foreground(hwnd, SW_RESTORE)
    if config.get("iisu_fullscreen"):
        make_fullscreen(hwnd)
        hide_emulator_toolbar()


def bring_emulator_to_foreground(pid: int) -> bool:
    """Poll for the new process's main window (it takes a moment to appear
    after Popen returns) and force it to the foreground once found. Returns
    whether the window was found."""
    hwnd = wait_for_window_by_pid(pid)
    if hwnd is None:
        print("[bridge] could not locate emulator window to focus")
        return False
    force_foreground(hwnd)
    # Give the window a moment to finish becoming active before clicking it.
    time.sleep(0.3)
    nudge_focus_with_click(hwnd)
    return True


def wait_and_restore_iisu(process: subprocess.Popen, config: dict, emulator_name: str) -> None:
    """Runs on a background thread: waits for the emulator to close (whether
    normally or via the quit hotkey), then un-hides and refocuses the iiSU
    AVD window, mirroring how the real Android launcher reappears once a
    game exits.

    A non-zero exit code is only a heuristic for "this launch actually
    failed," not a certainty, some emulators exit non-zero on a normal
    quit too, but it's the only signal available for the class of
    failure that shows its own error dialog and waits for it to be
    dismissed rather than crashing outright (confirmed live: RPCS3's
    missing-boot-target dialog, DuckStation's missing-SBI-file dialog,
    neither exits until someone clicks through it, so there's no
    "crashed immediately" moment to catch, only the eventual exit code
    once they do). Logged either way; only notified when it looks like a
    real failure, worth interrupting someone over."""
    process.wait()
    with current_process_lock:
        global current_process
        if current_process is process:
            current_process = None
    if process.returncode not in (0, None):
        log_launch(f"EXITED: {emulator_name} exited with code {process.returncode} (possible launch error)", notify=True, notify_title=emulator_name)
    else:
        log_launch(f"EXITED: {emulator_name} exited normally")
    show_iisu_window(config)


def _register_hotkey(hotkey_config: dict, hotkey_id: int, purpose: str) -> str | None:
    modifiers = MOD_NOREPEAT
    for name in hotkey_config.get("modifiers", []):
        modifiers |= MODIFIER_FLAGS.get(name.lower(), 0)

    key = hotkey_config.get("key", "q")
    vk = ord(key.upper()[0])

    if not user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
        print(f"[bridge] failed to register {purpose} hotkey ({'+'.join(hotkey_config.get('modifiers', []))}+{key})")
        return None
    return "+".join([*hotkey_config.get("modifiers", []), key]).upper()


def shutdown_everything() -> None:

    """Closes iiSU and shuts down the whole Android subsystem: terminates
    whatever PC emulator is currently running (if any), then hands off to
    stop_iisu_pc.py for the graceful AVD/bridge teardown and exits this
    process. Runs as a separate process because this one is about to exit
    itself, and because stop_iisu_pc.py needs to be able to kill this
    bridge process by PID. Detached and logged to stop.log by default
    rather than given a visible console, a console window for a script
    that just prints a handful of status lines and exits is pure clutter,
    unless config.json's "debug_show_console_windows" says otherwise."""
    with current_process_lock:
        proc = current_process
        native_hwnd = current_native_window
        native_pid = current_native_pid

    if proc is not None and proc.poll() is None:
        proc.terminate()

    # Prefer the tracked PID for native games. A game can destroy/recreate its
    # HWND during a resolution or display-mode change while its process remains
    # alive, so the HWND alone is not sufficient for full shutdown.
    if native_pid and _pid_is_running(native_pid):
        if not _terminate_native_pid(native_pid):
            if native_hwnd is not None and user32.IsWindow(native_hwnd):
                close_window(native_hwnd)
    elif native_hwnd is not None and user32.IsWindow(native_hwnd):
        close_window(native_hwnd)

    end_game_handoff()

    try:
        debug_console = load_config().get("debug_show_console_windows", False)
    except ConfigMissingError:
        debug_console = False

    if debug_console:
        subprocess.Popen(
            [sys.executable, str(STOP_SCRIPT)],
            creationflags=new_console_creationflags(),
            cwd=str(STOP_SCRIPT.parent),
        )
    else:
        stop_log_file = open(STOP_LOG_PATH, "wb")
        try:
            subprocess.Popen(
                [sys.executable, str(STOP_SCRIPT)],
                **detached_popen_kwargs(),
                stdin=subprocess.DEVNULL,
                stdout=stop_log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                cwd=str(STOP_SCRIPT.parent),
            )
        finally:
            stop_log_file.close()
    debug_log("shutdown_everything(): about to os._exit(0)")
    os._exit(0)


def hotkey_listener(config: dict) -> None:
    """Registers the *optional*, separate full-shutdown hotkey (config.json's
    "shutdown_hotkey") and dispatches it as it's pressed. Runs on its own
    thread with its own message loop, since RegisterHotKey delivers
    WM_HOTKEY via the calling thread's queue.

    quit_hotkey no longer goes through this at all, see quit_key_watcher,
    which runs as its own separate thread/mechanism (GetAsyncKeyState
    polling, not RegisterHotKey) since it now needs to tell a quick tap
    apart from a multi-second hold, something RegisterHotKey's single
    fire-once-per-press model has no way to express. shutdown_hotkey stays
    on the old mechanism as a distinct, independent combo for anyone who
    wants one in addition to holding quit_hotkey, it's optional (None
    skips registration entirely) since a fresh install's default
    quit_hotkey (Escape) already covers full shutdown via a hold, with no
    second combo needed."""
    shutdown_hotkey_config = config.get("shutdown_hotkey")
    shutdown_label = _register_hotkey(shutdown_hotkey_config, SHUTDOWN_HOTKEY_ID, "full shutdown") if shutdown_hotkey_config else None
    if shutdown_label:
        print(f"[bridge] {shutdown_label} will also close iiSU and shut down the AVD entirely")

    msg = wintypes.MSG()
    while True:
        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result <= 0:
            break
        if msg.message != WM_HOTKEY:
            continue
        if msg.wParam == SHUTDOWN_HOTKEY_ID:
            print("[bridge] shutdown hotkey pressed, closing iiSU and the AVD...")
            shutdown_everything()


def quit_tap_action(label: str = "quit") -> None:
    """Runs whatever a quit tap means right now: force-quit the running
    game and return to iiSU if one's active, or close iiSU and shut down
    the AVD entirely if nothing's running. Shared by the keyboard quit
    hotkey (quit_key_watcher) and the controller quit chord
    (controller_bridge.ControllerBridge), so both trigger identical
    behavior through one code path. label is just what gets printed/
    logged to say what triggered it."""
    with current_process_lock:
        proc = current_process
        native_hwnd = current_native_window
        native_pid = current_native_pid
        handoff_active = game_handoff_active
    if native_hwnd is not None and user32.IsWindow(native_hwnd):
        print(f"[bridge] {label}, quitting native game")
        log_launch(f"QUIT: {label}, native HWND {native_hwnd}, PID {native_pid}")
        if native_pid and _pid_is_running(native_pid):
            # Once a native game has been identified, its owning PID is
            # authoritative. current_process may only be a launcher or
            # wrapper executable that remains alive beside it.
            if not _terminate_native_pid(native_pid):
                print("[bridge] taskkill failed; falling back to WM_CLOSE")
                close_window(native_hwnd)
        else:
            close_window(native_hwnd)
        if proc is not None and proc.poll() is None and proc.pid != native_pid:
            try:
                proc.terminate()
            except Exception:
                pass
    elif native_pid and _pid_is_running(native_pid):
        print(f"[bridge] {label}, native game window changed, terminating tracked PID {native_pid}")
        log_launch(f"QUIT: {label}, native HWND changed; terminating PID {native_pid}")
        _terminate_native_pid(native_pid)
        if proc is not None and proc.poll() is None and proc.pid != native_pid:
            try:
                proc.terminate()
            except Exception:
                pass
    elif proc is not None and proc.poll() is None:
        # No native HWND/PID has been established, so this is still a
        # normal subprocess-backed emulator/game launch.
        print(f"[bridge] {label}, terminating emulator")
        log_launch(f"QUIT: {label}, terminating emulator PID {proc.pid}")
        proc.terminate()
    elif handoff_active:
        # Steam may still be between URI invocation and creation of the
        # game's real window. Queue the quit instead of either shutting
        # down iiSU-PC or silently dropping the command.
        #
        # IMPORTANT: do not acquire current_process_lock here. This
        # function already sampled the handoff state under that lock
        # above; taking the same non-reentrant Lock a second time could
        # permanently deadlock the calling thread.
        global pending_native_quit
        pending_native_quit = True
        print(f"[bridge] {label} during native launch handoff, quit queued")
        log_launch(f"QUIT: {label} during native launch handoff; queued until HWND/PID is tracked")
    else:
        print(f"[bridge] {label} with no emulator/native game running, closing iiSU and the AVD entirely...")
        log_launch(f"QUIT: {label}, no tracked emulator/native HWND; shutting down iiSU-PC")
        shutdown_everything()


def quit_key_watcher(config: dict) -> None:
    """Polls quit_hotkey's key (default: Escape, no modifiers) via
    GetAsyncKeyState instead of RegisterHotKey, on its own thread, and
    runs quit_tap_action() on release. A tap's meaning depends on
    whether an emulator is actually running: with one running, it
    force-quits it and returns to iiSU; with none running, already
    sitting at iiSU itself, there's nothing to quit back to, so it
    closes iiSU and the AVD entirely instead."""
    hotkey_config = config["quit_hotkey"]
    key_name = hotkey_config.get("key", "escape")
    vk = resolve_vk(key_name)
    modifier_names = hotkey_config.get("modifiers", [])
    modifier_vks = [MODIFIER_VK[name] for name in modifier_names if name in MODIFIER_VK]
    label = "+".join([*modifier_names, key_name]).upper()

    print(f"[bridge] {label}: tap to quit the running emulator (or close iiSU entirely if none is running)")

    was_down = False
    while True:
        time.sleep(KEY_POLL_INTERVAL)
        is_down = _is_key_down(vk) and all(_is_key_down(m) for m in modifier_vks)
        if is_down and not was_down:
            debug_log(f"quit hotkey DOWN: {label}")
        elif was_down and not is_down:
            debug_log(f"quit hotkey UP: {label}")
            quit_tap_action(f"{label} tapped")
        was_down = is_down


def _linux_key_watcher(config: dict) -> None:
    """Linux equivalent of hotkey_listener()/quit_key_watcher() above:
    Windows' RegisterHotKey/GetMessageW has no Linux equivalent, so this
    reads raw key events directly off /dev/input instead (works
    regardless of desktop environment, X11 or Wayland, unlike a
    toolkit-level global-hotkey API). Needs read access to /dev/input,
    normally means being in the `input` group.

    Escape mirrors the configurable keyboard quit hotkey (tap: quit_tap_
    action, hold 1.2s: full shutdown); Alt+F4/Ctrl+Q/Ctrl+Alt+X are fixed
    shutdown shortcuts, and Ctrl+Shift+Esc is a fixed quit-tap shortcut,
    none of these three are configurable the way the Escape binding is
    (see BUTTON_NAME_TO_BIT/config.json's quit_hotkey for that)."""
    import glob
    import select
    import struct

    event_format = "qqHHi" if struct.calcsize("l") == 8 else "llHHi"
    event_size = struct.calcsize(event_format)

    EV_KEY = 1
    KEY_ESC, KEY_LEFTCTRL, KEY_RIGHTCTRL = 1, 29, 97
    KEY_LEFTALT, KEY_RIGHTALT = 56, 100
    KEY_LEFTSHIFT, KEY_RIGHTSHIFT = 42, 54
    KEY_F4, KEY_X, KEY_Q = 62, 45, 16
    HOLD_SECONDS = 1.2

    print("[bridge] Linux keyboard quit hotkeys active: Esc / Alt+F4 / Ctrl+Q / Ctrl+Alt+X")

    pressed_keys: set[int] = set()
    esc_down_time: float | None = None
    last_rescan = 0.0
    fds: dict[int, str] = {}

    def update_fds() -> None:
        nonlocal fds
        current_paths = set(fds.values())
        candidate_paths = set(glob.glob("/dev/input/by-id/*-kbd") + glob.glob("/dev/input/by-id/*-event-kbd"))
        if not candidate_paths:
            candidate_paths = set(glob.glob("/dev/input/event*"))
        for path in candidate_paths - current_paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                fds[fd] = path
            except OSError:
                pass
        for fd in [fd for fd, path in fds.items() if not os.path.exists(path)]:
            try:
                os.close(fd)
            except OSError:
                pass
            del fds[fd]

    update_fds()

    while True:
        now = time.time()
        if now - last_rescan > 5.0:
            last_rescan = now
            update_fds()

        if esc_down_time and (now - esc_down_time > HOLD_SECONDS):
            print("[bridge] Escape held on Linux, shutting down iiSU and the AVD...")
            esc_down_time = None
            shutdown_everything()
            return

        if not fds:
            time.sleep(0.5)
            continue

        try:
            ready, _, _ = select.select(list(fds.keys()), [], [], 0.05)
        except OSError:
            time.sleep(0.05)
            continue

        for fd in ready:
            try:
                chunk = os.read(fd, event_size * 32)
            except OSError:
                continue
            if not chunk or len(chunk) < event_size:
                continue
            for offset in range(0, len(chunk) - event_size + 1, event_size):
                _sec, _usec, ev_type, ev_code, ev_value = struct.unpack(event_format, chunk[offset:offset + event_size])
                if ev_type != EV_KEY:
                    continue

                if ev_value == 1:  # key down
                    pressed_keys.add(ev_code)
                    is_ctrl = KEY_LEFTCTRL in pressed_keys or KEY_RIGHTCTRL in pressed_keys
                    is_alt = KEY_LEFTALT in pressed_keys or KEY_RIGHTALT in pressed_keys
                    is_shift = KEY_LEFTSHIFT in pressed_keys or KEY_RIGHTSHIFT in pressed_keys

                    if ev_code == KEY_ESC:
                        esc_down_time = time.time()
                    if is_alt and ev_code == KEY_F4:
                        print("[bridge] Alt+F4 pressed on Linux, shutting down iiSU...")
                        shutdown_everything()
                        return
                    if is_ctrl and ev_code == KEY_Q:
                        print("[bridge] Ctrl+Q pressed on Linux, shutting down iiSU...")
                        shutdown_everything()
                        return
                    if is_ctrl and is_alt and ev_code == KEY_X:
                        print("[bridge] Ctrl+Alt+X pressed on Linux, shutting down iiSU...")
                        shutdown_everything()
                        return
                    if is_ctrl and is_shift and ev_code == KEY_ESC:
                        print("[bridge] Ctrl+Shift+Esc pressed on Linux, closing running game / iiSU...")
                        quit_tap_action("Ctrl+Shift+Escape pressed")

                elif ev_value == 0:  # key up
                    pressed_keys.discard(ev_code)
                    if ev_code == KEY_ESC:
                        held = (time.time() - esc_down_time) if esc_down_time else 0
                        esc_down_time = None
                        if held < HOLD_SECONDS:
                            debug_log("Escape key released on Linux")
                            quit_tap_action("Escape tapped")


def load_windows_apps() -> dict:
    """Load native Windows app mappings fresh for every launch."""
    if not WINDOWS_APPS_PATH.is_file():
        return {}
    try:
        data = json.loads(WINDOWS_APPS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log_launch(f"FAILED: could not read windows_apps.json ({e})", notify=True)
        return {}
    if not isinstance(data, dict):
        log_launch("FAILED: windows_apps.json must contain a JSON object", notify=True)
        return {}
    return {str(name).casefold(): entry for name, entry in data.items()}


def wait_and_restore_iisu_for_window(hwnd: int, config: dict, app_name: str) -> None:
    """Track a native game across HWND recreation and restore iiSU on exit."""
    global current_process, current_native_window, current_native_pid

    with current_process_lock:
        tracked_pid = current_native_pid

    if not tracked_pid:
        # Without a PID, the original HWND is our only reliable lifetime
        # signal. Preserve the conservative behavior and wait for it to close.
        wait_for_window_to_close(hwnd)
    else:
        tracked_hwnd = hwnd

        while _pid_is_running(tracked_pid):
            if tracked_hwnd is not None and user32.IsWindow(tracked_hwnd):
                time.sleep(0.25)
                continue

            # The process is still alive but its tracked top-level window has
            # disappeared. Games commonly recreate HWNDs during display-mode
            # or resolution changes, so try to adopt a stable replacement
            # owned by the same PID.
            replacement = wait_for_visible_window_by_pid(
                tracked_pid,
                timeout=5.0,
                stable_seconds=0.5,
            )

            if replacement is not None:
                tracked_hwnd = replacement
                with current_process_lock:
                    if current_native_pid != tracked_pid:
                        return
                    current_native_window = replacement
                debug_log(
                    f"native app replaced HWND: {app_name!r}; "
                    f"tracked_pid={tracked_pid}; hwnd={replacement}"
                )
            else:
                # The PID is still the authoritative lifetime signal. Keep
                # waiting rather than treating a temporarily windowless game
                # as exited, but clear current_native_window so the tracker
                # never references a destroyed HWND.
                tracked_hwnd = None
                with current_process_lock:
                    if current_native_pid == tracked_pid:
                        current_native_window = None
                time.sleep(0.25)

    with current_process_lock:
        if current_native_pid != tracked_pid:
            return
        current_native_window = None
        current_native_pid = None
        current_process = None

    debug_log(f"native app exited: {app_name!r}; tracked_pid={tracked_pid}")
    log_launch(f"EXITED: Windows app {app_name} process ended")
    show_iisu_window(config)


def launch_windows_app(app_name: str, config: dict) -> bool:
    global pending_native_quit
    """Launch a native Windows app or registered protocol URI from windows_apps.json."""

    # Defend this launch path independently as well. handle_request() normally
    # rejects overlapping launches, but launch_windows_app() must not overwrite
    # active process/window tracking if called from another path.
    if is_game_running():
        log_launch(
            f"IGNORED: Windows app '{app_name}' requested while a game is already running",
            notify=True,
            notify_title=app_name,
        )
        return True

    app = load_windows_apps().get(app_name.casefold())
    if app is None:
        return False
    if not isinstance(app, dict):
        log_launch(f"FAILED: Windows app '{app_name}' has an invalid config entry", notify=True)
        return True

    # Backward compatibility: entries created before URI support have no
    # explicit type and are treated as normal executable launches.
    launch_type = str(app.get("type", "executable")).lower()
    if launch_type not in {"executable", "uri"}:
        log_launch(
            f"FAILED: Windows app '{app_name}' has unknown launch type '{launch_type}'",
            notify=True,
            notify_title=app_name,
        )
        return True

    executable = None
    configured_args = []
    working_dir = None
    uri = None

    if launch_type == "uri":
        uri_value = app.get("uri")
        if not isinstance(uri_value, str) or not uri_value.strip():
            log_launch(f"FAILED: Windows app '{app_name}' has no URI configured", notify=True, notify_title=app_name)
            return True
        uri = uri_value.strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://\S+$", uri):
            log_launch(f"FAILED: Windows app '{app_name}' has an invalid protocol URI", notify=True, notify_title=app_name)
            return True
    else:
        exe_value = app.get("exe")
        if not isinstance(exe_value, str) or not exe_value.strip():
            log_launch(f"FAILED: Windows app '{app_name}' has no executable configured", notify=True)
            return True

        executable = Path(os.path.expandvars(os.path.expanduser(exe_value)))
        if not executable.is_file():
            log_launch(f"FAILED: Windows app '{app_name}' executable not found: {executable}", notify=True, notify_title=app_name)
            return True

        configured_args = app.get("args", [])
        if not isinstance(configured_args, list) or not all(isinstance(arg, str) for arg in configured_args):
            log_launch(f"FAILED: Windows app '{app_name}' args must be a list of strings", notify=True)
            return True

        working_dir_value = app.get("working_dir")
        working_dir = Path(os.path.expandvars(os.path.expanduser(str(working_dir_value)))) if working_dir_value else executable.parent
        if not working_dir.is_dir():
            log_launch(f"FAILED: Windows app '{app_name}' working directory not found: {working_dir}", notify=True, notify_title=app_name)
            return True

    # Cut iiSU off immediately, before Steam or the executable is invoked.
    # Native URI launches can take seconds before their real HWND exists.
    begin_game_handoff()

    existing_windows = list_visible_windows()
    show_overlay = config.get("show_boot_overlay", True) and not config.get("debug_show_console_windows", False)
    overlay = boot_overlay.show(f"Waiting on {app_name}...") if show_overlay else None
    process = None
    hwnd = None
    try:
        iisu_hwnd = find_window_by_title(config["iisu_window_title"])
        if iisu_hwnd is not None:
            user32.ShowWindow(iisu_hwnd, SW_MINIMIZE)
        else:
            print("[bridge] could not locate iiSU window to hide")

        if launch_type == "uri":
            print(f"[bridge] launching Windows URI: {uri}")
            log_launch(f"LAUNCHED: Windows app {app_name}, URI {uri}")
            open_uri(uri)
        else:
            args = [str(executable), *configured_args]
            print(f"[bridge] launching Windows app: {args}")
            log_launch(f"LAUNCHED: Windows app {app_name}, {args}")
            process = subprocess.Popen(args, cwd=str(working_dir))
            with current_process_lock:
                global current_process
                current_process = process

        # Determine any launcher or helper processes to exclude while waiting
        # for the real top-level application window.
        excluded_processes = {"steam.exe", "steamwebhelper.exe"}
        app_excluded = app.get("excluded_processes") or app.get("exclude_processes")
        if isinstance(app_excluded, list):
            excluded_processes.update(str(p).lower() for p in app_excluded)

        # Both URI handlers and executables often create transient launcher UI
        # or splash screens before the real app window. Require the candidate
        # window to survive for stable_seconds and ignore common launcher
        # processes so a loading screen is not mistaken for the game itself.
        hwnd = wait_for_new_visible_window_excluding_processes(
            existing_windows,
            excluded_processes,
            timeout=60.0,
            stable_seconds=1.5,
        )
        if hwnd is None:
            log_launch(
                f"FAILED: Windows app '{app_name}' launched but no new visible window was detected",
                notify=True,
                notify_title=app_name,
            )
            if process is not None:
                with current_process_lock:
                    if current_process is process:
                        current_process = None
                try:
                    process.terminate()
                except Exception:
                    pass
            show_iisu_window(config)
            return True

        native_pid = _window_pid(hwnd)
        with current_process_lock:
            global current_native_window, current_native_pid
            current_native_window = hwnd
            current_native_pid = native_pid
        debug_log(f"TRACKING native app={app_name!r} HWND={hwnd} PID={native_pid}")
        log_launch(f"TRACKING: Windows app {app_name}, HWND {hwnd}, PID {native_pid}")
        print(f"[bridge] tracking native game window HWND={hwnd}, PID={native_pid}")

        # Start lifetime tracking immediately after publishing the native
        # HWND/PID. Later foreground/quit handling may raise, but restoration
        # must already have an owner once the game is globally tracked.
        threading.Thread(
            target=wait_and_restore_iisu_for_window,
            args=(hwnd, config, app_name),
            daemon=True,
        ).start()

        with current_process_lock:
            quit_was_queued = pending_native_quit
            pending_native_quit = False
        if quit_was_queued:
            print("[bridge] honoring quit command queued during native launch handoff")
            log_launch(f"QUIT: force-quitting {app_name} immediately after native tracking became available")
            if native_pid and _pid_is_running(native_pid):
                if not _terminate_native_pid(native_pid) and user32.IsWindow(hwnd):
                    close_window(hwnd)
            elif user32.IsWindow(hwnd):
                close_window(hwnd)
        else:
            force_foreground(hwnd)
    except Exception as e:
        debug_log(
            f"FAILED: unexpected error while launching Windows app {app_name!r}\n"
            + traceback.format_exc().rstrip()
        )
        log_launch(
            f"FAILED: could not launch Windows app '{app_name}' ({e})",
            notify=True,
            notify_title=app_name,
        )
        with current_process_lock:
            tracked_hwnd = current_native_window
        if tracked_hwnd is None and process is not None:
            with current_process_lock:
                if current_process is process:
                    current_process = None
            try:
                process.terminate()
            except Exception:
                pass
        recover_from_request_exception(config)
        return True
    finally:
        boot_overlay.close(overlay)

    return True


def launch_steam_game(app_id: str, config: dict) -> None:
    """Launch a GameNative Steam title and track its native Windows game window."""
    global current_native_window, current_native_pid, pending_native_quit

    # GameNative ultimately launches a normal Windows Steam game, so use the
    # same handoff/tracking lifecycle as native URI entries from windows_apps.
    begin_game_handoff()

    existing_windows = list_visible_windows()
    show_overlay = config.get("show_boot_overlay", True) and not config.get("debug_show_console_windows", False)
    overlay = boot_overlay.show(f"Waiting on Steam app {app_id}...") if show_overlay else None
    hwnd = None

    try:
        iisu_hwnd = find_window_by_title(config["iisu_window_title"])
        if iisu_hwnd is not None:
            user32.ShowWindow(iisu_hwnd, SW_MINIMIZE)
        else:
            print("[bridge] could not locate iiSU window to hide")

        print(f"[bridge] launching Steam app {app_id}...")
        open_uri(f"steam://rungameid/{app_id}")

        # Steam itself may create transient launcher windows before the game's
        # real top-level window. Ignore Steam-owned UI and adopt the persistent
        # window created by the actual game process.
        hwnd = wait_for_new_visible_window_excluding_processes(
            existing_windows,
            {"steam.exe", "steamwebhelper.exe"},
            timeout=60.0,
            stable_seconds=1.5,
        )
        if hwnd is None:
            log_launch(
                f"FAILED: Steam app {app_id} launched but no new visible game window was detected",
                notify=True,
            )
            show_iisu_window(config)
            return

        native_pid = _window_pid(hwnd)
        with current_process_lock:
            current_native_window = hwnd
            current_native_pid = native_pid

        debug_log(
            f"TRACKING GameNative Steam app={app_id!r} HWND={hwnd} PID={native_pid}"
        )
        log_launch(
            f"TRACKING: Steam app {app_id} via GameNative, HWND {hwnd}, PID {native_pid}"
        )
        print(
            f"[bridge] tracking GameNative Steam window HWND={hwnd}, PID={native_pid}"
        )

        # Publish tracking and establish its restoration watcher before any
        # later foreground or queued-quit operation can raise.
        threading.Thread(
            target=wait_and_restore_iisu_for_window,
            args=(hwnd, config, f"Steam app {app_id}"),
            daemon=True,
        ).start()

        with current_process_lock:
            quit_was_queued = pending_native_quit
            pending_native_quit = False

        if quit_was_queued:
            print("[bridge] honoring quit command queued during GameNative Steam handoff")
            log_launch(
                f"QUIT: force-quitting Steam app {app_id} immediately after native tracking became available"
            )
            if native_pid and _pid_is_running(native_pid):
                if not _terminate_native_pid(native_pid) and user32.IsWindow(hwnd):
                    close_window(hwnd)
            elif user32.IsWindow(hwnd):
                close_window(hwnd)
        else:
            force_foreground(hwnd)

    except Exception as e:
        debug_log(
            f"FAILED: unexpected error while launching GameNative Steam app {app_id!r}\n"
            + traceback.format_exc().rstrip()
        )
        log_launch(
            f"FAILED: could not launch Steam app {app_id} via GameNative ({e})",
            notify=True,
        )
        recover_from_request_exception(config)
        return
    finally:
        boot_overlay.close(overlay)


def launch_package(raw_intent: str) -> str | None:
    cmp_match = INTENT_CMP_RE.search(raw_intent)
    return cmp_match.group(1).split("/")[0] if cmp_match else None


def belongs_on_android(raw_intent: str, config: dict) -> bool:
    """Whether a launch from iiSU is an Android app no PC program stands in
    for, such as Chrome or the Play Store. iiSU sends those through the same
    call as games, so the patch used to send them here, where they failed
    with "no known PC emulator mapped" and could never be opened. Anything
    this bridge would handle stays on the PC: a mapped emulator package,
    Winlator for Windows apps, or GameNative for Steam."""
    package = launch_package(raw_intent)
    if package is None:
        return True
    if package in WINDOWS_LAUNCH_PACKAGES or package == GAMENATIVE_PACKAGE:
        return False
    return not any(package.startswith(prefix) for prefix in config.get("emulators", {}))


def android_start_command(raw_intent: str) -> str | None:
    """The `am start` that opens an app the way iiSU asked to: its component,
    action, categories and flags, all of which Intent.toString() shows in
    full. None when the launch names no component to start."""
    cmp_match = INTENT_CMP_RE.search(raw_intent)
    if not cmp_match:
        return None
    args = ["am", "start", "-n", cmp_match.group(1)]
    if act := INTENT_ACT_RE.search(raw_intent):
        args += ["-a", act.group(1)]
    if cat := INTENT_CAT_RE.search(raw_intent):
        for category in filter(None, (c.strip() for c in cat.group(1).split(","))):
            args += ["-c", category]
    if flg := INTENT_FLG_RE.search(raw_intent):
        args += ["-f", flg.group(1)]
    return " ".join(adb_client.quote(arg) for arg in args)


def start_on_android(raw_intent: str) -> tuple[bool, str]:
    """Opens an Android app for an iiSU patched before it could do that
    itself. Returns whether it started, and what am said."""
    command = android_start_command(raw_intent)
    if command is None:
        return False, "the launch names no app to start"
    status, output = adb_client.shell(command, timeout=15)
    return status == 0 and "Error" not in output, output.strip()


def answer_launch(conn, raw_intent: str) -> bool:
    """Decides, before any of the slower work in handle_request(), whether
    this launch is an Android app, and gets it started there. A patched iiSU
    that reads the answer starts it itself; for one patched before that, the
    bridge starts it over adb. Returns True when the launch went to Android,
    so there is nothing left to do here. A config that can't be read keeps the
    launch here, where handle_request() reports why."""
    try:
        on_android = belongs_on_android(raw_intent, load_config())
    except ConfigMissingError:
        on_android = False
    package = launch_package(raw_intent) or "a launch with no app named"
    if REPLY_CAPABLE_LINE in raw_intent.splitlines():
        try:
            conn.sendall(REPLY_LAUNCH_ON_ANDROID if on_android else REPLY_HANDLED_ON_PC)
        except OSError:
            pass  # iiSU gave up waiting; a launch it sent is the PC's from here
        if on_android:
            log_launch(f"ANDROID: handed {package} back to Android")
    elif on_android:
        started, output = start_on_android(raw_intent)
        if started:
            log_launch(f"ANDROID: started {package} on Android for an older iiSU patch")
        else:
            log_launch(f"FAILED: couldn't start {package} on Android ({output})", notify=True)
    return on_android


def handle_request(raw_intent: str) -> None:
    request_started = time.monotonic()
    print(f"[bridge] received: {raw_intent}")

    # Reloaded fresh per request (not once at startup) so edits made in
    # the Manager take effect on the very next launch without restarting
    # the bridge process.
    try:
        config = load_config()
    except ConfigMissingError as e:
        print(f"[bridge] {e}")
        return

    # Refuse a second launch while one is already tracked. Without this,
    # a double-fired selection can overwrite the current tracking state and
    # leave the first game running but unreachable by the normal quit flow.
    if is_game_running():
        log_launch(
            "IGNORED: launch request while a game is already running",
            notify=True,
        )
        return

    # Native Windows applications: iiSU's Windows platform routes .pcgame
    # placeholders through Winlator. The placeholder basename is our app ID.
    cmp_match = INTENT_CMP_RE.search(raw_intent)
    if cmp_match:
        component = cmp_match.group(1)
        package = component.split("/")[0]
        if package in WINDOWS_LAUNCH_PACKAGES:
            shortcut_match = re.search(r"^EXTRA:shortcut_path=(.+)$", raw_intent, re.MULTILINE)
            if not shortcut_match:
                log_launch(f"FAILED: Windows launch from '{package}' had no shortcut_path extra", notify=True)
                return

            shortcut_path = shortcut_match.group(1).strip()
            app_name = Path(shortcut_path.replace("\\", "/")).stem
            print(f"[bridge] Windows app requested: {app_name}")

            hold_iisu_behind_stub(package)
            if not launch_windows_app(app_name, config):
                release_iisu_from_stub()
                log_launch(
                    f"FAILED: Windows app '{app_name}' is not registered in windows_apps.json",
                    notify=True,
                    notify_title=app_name,
                )
            return

    cmp_match = INTENT_CMP_RE.search(raw_intent)
    dat_match = INTENT_DAT_RE.search(raw_intent)
    clip_uris = [
        line.removeprefix("CLIPURI:")
        for line in raw_intent.splitlines()
        if line.startswith("CLIPURI:")
    ]
    # RetroArch launches (and possibly other libretro-frontend launches)
    # pass the ROM and the exact core to use as plain Intent extras instead
    # of a data URI/ClipData, iiSU launches RetroArch exclusively this
    # way, never through the URI mechanism every other emulator here uses,
    # so these have to be parsed separately or RetroArch games never
    # launch regardless of how config.json's by_extension map is set up.
    # The smali patch already sends every extra as "EXTRA:key=value".
    extras = dict(
        line.removeprefix("EXTRA:").split("=", 1)
        for line in raw_intent.splitlines()
        if line.startswith("EXTRA:") and "=" in line
    )

    if not cmp_match:
        print("[bridge] no component in intent, ignoring")
        return

    component = cmp_match.group(1)
    package = component.split("/")[0]

    if package == GAMENATIVE_PACKAGE:
        app_id = extras.get("app_id")
        if app_id is None:
            log_launch("FAILED: GameNative launch with no app_id extra, can't tell Steam what to run", notify=True)
            return
        log_launch(f"LAUNCHED: Steam app {app_id} via GameNative")
        hold_iisu_behind_stub(package)
        launch_steam_game(app_id, config)
        return

    # iiSU passes the ROM file via ClipData (not the plain Intent data URI) at
    # least for single/multi-file discs; Intent.toString() only shows a
    # truncated placeholder for ClipData, so the patched app sends the real
    # URI(s) separately as CLIPURI: lines.
    data_uri = clip_uris[0] if clip_uris else (dat_match.group(1) if dat_match else None)

    search_roots = [Path(p) for p in config["search_roots"]]
    roms_dir = Path(config["roms_dir"])

    if data_uri:
        rom_filename = unquote(data_uri).rsplit("/", 1)[-1]
    elif "ROM" in extras:
        # Already a plain filesystem path, not URI-encoded, no unquote().
        rom_filename = extras["ROM"].rsplit("/", 1)[-1]
    else:
        rom_filename = None

    profile = find_emulator_for_package(package, config["emulators"], rom_filename, extras.get("LIBRETRO"))
    if profile is None:
        log_launch(f"FAILED: no known PC emulator mapped for package '{package}' (rom '{rom_filename}')", notify=True)
        return

    # Per-game settings from the Manager's Console page: another emulator,
    # extra flags, extra environment, or a command to run first. Applied
    # after normal routing so a game with no override is untouched.
    override = game_overrides.get_override(config, rom_filename)
    if override:
        profile, override_notes = game_overrides.apply_to_profile(profile, override, config["emulators"], IS_WINDOWS)
        log_launch(f"OVERRIDE for '{rom_filename}': {', '.join(override_notes) or 'environment/pre-launch only'}")

    path_cache = load_path_cache()

    executable = find_executable(profile["exe_names"], search_roots, path_cache)
    if executable is None:
        save_path_cache(path_cache)
        log_launch(f"FAILED: none of {profile['exe_names']} found under {search_roots} (rom '{rom_filename}')", notify=True)
        return

    rom_path = None
    if rom_filename:
        rom_path = find_rom(rom_filename, roms_dir, path_cache)
        if rom_path is None:
            log_launch(f"FAILED: rom '{rom_filename}' not found under {roms_dir}", notify=True, notify_title=friendly_emulator_name(executable))

    save_path_cache(path_cache)

    core_dll = core_dll_from_pre_args(profile["pre_args"])
    if core_dll:
        ensure_retroarch_core(executable.parent, core_dll)

    args = build_pc_launch_args(profile, executable, rom_path)

    # Covers the gap between iiSU's window minimizing and the real PC
    # emulator's own window appearing and taking the foreground, without
    # it, that moment shows raw desktop. Skipped when debug_show_console_
    # windows is on, since a fullscreen overlay would just hide the
    # console windows that setting exists to show.
    show_overlay = config.get("show_boot_overlay", True) and not config.get("debug_show_console_windows", False)
    overlay = boot_overlay.show(f"Waiting on {friendly_emulator_name(executable)}...") if show_overlay else None
    try:
        iisu_hwnd = find_window_by_title(config["iisu_window_title"])
        if iisu_hwnd is not None:
            user32.ShowWindow(iisu_hwnd, SW_MINIMIZE)
        else:
            print("[bridge] could not locate iiSU window to hide")

        if override and override["pre_launch"]:
            pre_ok, pre_message = game_overrides.run_pre_launch(override["pre_launch"], IS_WINDOWS)
            log_launch(f"{'PRE-LAUNCH' if pre_ok else 'PRE-LAUNCH PROBLEM'}: {pre_message}")

        print(f"[bridge] launching: {args}")
        log_launch(f"LAUNCHED: {friendly_emulator_name(executable)}, {args}")
        hold_iisu_behind_stub(package)
        process = subprocess.Popen(
            args, cwd=str(executable.parent), env=game_overrides.launch_env(override) if override else None
        )
        with current_process_lock:
            global current_process
            current_process = process

        # Establish lifetime tracking before any foreground/focus operation
        # that can raise. Once current_process is visible globally, there must
        # already be a watcher capable of restoring iiSU when it exits.
        threading.Thread(
            target=wait_and_restore_iisu,
            args=(process, config, friendly_emulator_name(executable)),
            daemon=True,
        ).start()

        if bring_emulator_to_foreground(process.pid):
            log_launch(f"ON SCREEN: {friendly_emulator_name(executable)} {time.monotonic() - request_started:.1f}s after iiSU asked")
    finally:
        boot_overlay.close(overlay)


def is_game_running() -> bool:
    """True while any PC-side game is active.

    ControllerBridge uses this to decide whether controller navigation should
    be forwarded into iiSU. Native/URI launches such as Steam often have no
    useful Popen handle, so current_native_window must count as a running game
    too; otherwise the hidden iiSU frontend continues receiving controller
    KeyEvents while the Steam game is in front.
    """
    with current_process_lock:
        if game_handoff_active:
            return True
        process_running = current_process is not None and current_process.poll() is None
        native_hwnd = current_native_window
        native_pid = current_native_pid

    # Keep slower PID probing outside the lock.
    native_running = (
        (native_hwnd is not None and user32.IsWindow(native_hwnd))
        or _pid_is_running(native_pid)
    )
    return process_running or native_running


def recover_from_request_exception(config: dict) -> None:
    """Recover frontend state after an unexpected request-handler exception.

    If a native game is already tracked and alive, leave iiSU suspended and
    let that game's lifecycle watcher restore it when the game exits. If the
    request failed before a viable native game was established, restore iiSU
    immediately so input/audio cannot remain stuck in handoff state.
    """
    with current_process_lock:
        native_hwnd = current_native_window
        native_pid = current_native_pid
        proc = current_process

    native_alive = (
        (native_hwnd is not None and user32.IsWindow(native_hwnd))
        or _pid_is_running(native_pid)
    )

    if native_alive:
        debug_log(
            "request exception occurred after native tracking was established; "
            f"leaving iiSU suspended for HWND={native_hwnd}, PID={native_pid}"
        )
        return

    # A subprocess-backed emulator may also already be running. Its normal
    # wait_and_restore_iisu thread usually owns frontend restoration, so
    # don't bring iiSU over it merely because later request handling
    # raised, but the exception may have occurred *before* handle_request
    # reached the line that starts that thread (e.g. bring_emulator_to_
    # foreground() itself raising right after the Popen succeeds), in which
    # case nothing is watching this process at all and iiSU would stay
    # suspended forever, even after the user closes the emulator normally.
    # wait_and_restore_iisu is safe to run twice for the same process (both
    # calls just block on the same Popen.wait()), so starting one here is a
    # harmless no-op in the common case and the only fix for the rare one.
    if proc is not None and proc.poll() is None:
        debug_log(
            "request exception occurred while tracked subprocess is still "
            f"running; leaving iiSU suspended for PID={proc.pid} and "
            "ensuring a watcher thread is running for it"
        )
        try:
            emulator_name = friendly_emulator_name(Path(proc.args[0]))
        except Exception:
            emulator_name = "the running emulator"
        threading.Thread(
            target=wait_and_restore_iisu, args=(proc, config, emulator_name), daemon=True
        ).start()
        return

    debug_log(
        "request exception left no live tracked game; restoring iiSU "
        "and ending any incomplete handoff"
    )
    show_iisu_window(config)


def main() -> None:
    bridge_started = time.monotonic()
    # Install bridge-specific crash logging only when this module is actually
    # running the bridge. The Manager imports helpers from launch_bridge and
    # must retain its own sys/threading exception hooks.
    sys.excepthook = _log_uncaught_exception
    if hasattr(threading, "excepthook"):
        threading.excepthook = _log_thread_exception

    debug_log("=" * 72)
    debug_log(
        f"launch_bridge starting; PID={os.getpid()}; Python={sys.version.split()[0]}"
    )
    version_banner = app_version.banner("launch_bridge")
    debug_log(version_banner)
    print(f"[bridge] {version_banner}")

    try:
        config = load_config()
    except ConfigMissingError as e:
        print(f"[bridge] {e}")
        sys.exit(1)
    log_launch(f"BRIDGE STARTED: {version_banner}")

    if IS_WINDOWS:
        threading.Thread(target=hotkey_listener, args=(config,), daemon=True).start()
        threading.Thread(target=quit_key_watcher, args=(config,), daemon=True).start()
    else:
        threading.Thread(target=_linux_key_watcher, args=(config,), daemon=True).start()

    controller_bridge = ControllerBridge(
        is_game_running,
        lambda label: quit_tap_action(label),
        config.get("controller_quit_chord"),
    )
    threading.Thread(target=controller_bridge.run, daemon=True).start()

    def _watch_avd_lifetime() -> None:
        """Self-terminates the bridge once the AVD it's serving is gone
        (closed some other way than Stop, e.g. a crash), rather than
        lingering as an orphaned process nobody can Start again over.

        Listens to the adb server's device change stream instead of asking
        every 2 seconds. A change that drops the VM is re-checked a few
        seconds later before shutting down, since the stream also reports a
        momentary "offline" that polling would mostly have missed. If the
        stream breaks, falls back to asking every 2 seconds, without ever
        starting an adb server Stop has just ended."""
        time.sleep(2)

        def vm_gone() -> bool:
            if vm_device_connected(adb_client.devices_text(start=False)):
                return False
            time.sleep(3)
            return not vm_device_connected(adb_client.devices_text(start=False))

        try:
            for listing in adb_client.track_devices():
                if not vm_device_connected(listing) and vm_gone():
                    break
            else:
                raise adb_client.AdbError("the device stream ended")
        except (OSError, adb_client.AdbError):
            while not vm_gone():
                time.sleep(2)
        debug_log("AVD is no longer running; shutting down bridge")
        os._exit(0)

    threading.Thread(target=_watch_avd_lifetime, daemon=True).start()

    def _toolbar_watcher() -> None:
        """Belt-and-suspenders: re-hides the standalone emulator's side
        toolbar periodically, in case something (a resize, a KWin rule
        not yet applied) brings it back after the initial hide."""
        time.sleep(2)
        while not _is_waydroid(config):
            try:
                hide_emulator_toolbar()
            except Exception:
                pass
            time.sleep(2)

    threading.Thread(target=_toolbar_watcher, daemon=True).start()

    # Launch iiSU directly rather than leaving the stock Android home
    # screen showing, whether this is a fresh boot or the bridge is being
    # restarted against an AVD that's already up.
    print("[bridge] launching iiSU...")
    launch_iisu(config)

    # If the AVD is already running when the bridge starts, apply the
    # fullscreen preference to it immediately rather than waiting for the
    # first game to exit.
    if config.get("iisu_fullscreen"):
        show_iisu_window(config)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, config["bridge_port"]))
        server.listen(5)
        print(f"[bridge] listening on {HOST}:{config['bridge_port']}, {time.monotonic() - bridge_started:.1f}s after the bridge started")
        while True:
            conn, addr = server.accept()
            with conn:
                chunks = []
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                payload = b"".join(chunks).decode("utf-8", errors="replace")
                if payload:
                    if answer_launch(conn, payload):
                        continue
                    try:
                        handle_request(payload)
                    except Exception:
                        debug_log(
                            "UNCAUGHT EXCEPTION while handling bridge request\n"
                            + traceback.format_exc().rstrip()
                        )
                        log_launch(
                            "FAILED: unexpected error while handling launch request",
                            notify=True,
                        )
                        # Do not force iiSU over a game that successfully
                        # launched before a later request-handling exception.
                        # The recovery helper restores iiSU only when no live
                        # tracked game remains.
                        recover_from_request_exception(config)


if __name__ == "__main__":
    main()
