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
     foreground window), then drops the overlay -- without it, the moment
     between iiSU minimizing and the emulator's window taking over would
     show raw desktop.
  5. Waits for the emulator to exit (either normally, or forced via the
     configured quit_hotkey) and restores the iiSU window (maximized if
     "iisu_fullscreen" is set), mirroring how the real Android launcher
     reappears once a game exits.

All configuration (ROM directory, emulator search paths, package->emulator
mappings, quit hotkey, display settings) lives in config.json next to this
script. Window-management helpers live in winapi.py, shared with
apply_display.py and manager.py. One Python stdlib script, no dependencies.
"""

import ctypes
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import traceback
import time
import urllib.request
import zipfile
from ctypes import wintypes
from pathlib import Path
from urllib.parse import unquote

import boot_overlay
import portable_sdk  # noqa: F401 -- imported for its import-time PATH fix (adb), not used directly here
from bridge_config import ConfigMissingError, load_config
from controller_bridge import ControllerBridge

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.emulator_defaults import (
    all_emulator_exe_names,
    retroarch_core_dll_for_android_core,
    standalone_profile_for_core_dll,
)
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
PATH_CACHE_PATH = Path(__file__).parent / ".path_cache.json"
LAUNCH_LOG_PATH = Path(__file__).parent / "launch_history.log"
WINDOWS_APPS_PATH = Path(__file__).parent / "windows_apps.json"
LIBRETRO_CORE_URL = "https://buildbot.libretro.com/nightly/windows/x86_64/latest/{core_dll}.zip"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

DEFAULT_IISU_COMPONENT = "com.iisulauncher/com.iisulauncher.launcher.StartupSafeModeActivity"

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

WM_HOTKEY = 0x0312
SHUTDOWN_HOTKEY_ID = 2
MODIFIER_FLAGS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000

# Virtual-key codes for GetAsyncKeyState, used by quit_key_watcher below --
# separate from MODIFIER_FLAGS (RegisterHotKey's own bitflags), which
# don't apply here since polling needs each modifier's actual key code.
MODIFIER_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
# Named (non-single-character) keys quit_hotkey's "key" can be set to,
# via manager.py's key-capture UI (Tk keysym, lowercased) -- anything not
# listed here falls back to ord(key.upper()[0]), which already covers
# every plain letter/digit key (the only kind this config supported before
# Escape became the default).
NAMED_KEY_VK = {
    "escape": 0x1B, "tab": 0x09, "return": 0x0D, "space": 0x20,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "prior": 0x21, "next": 0x22,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
KEY_POLL_INTERVAL = 0.03  # seconds -- responsive without busy-looping
DEFAULT_SHUTDOWN_HOLD_SECONDS = 5


def resolve_vk(key: str) -> int:
    return NAMED_KEY_VK.get(key.lower(), ord(key.upper()[0]) if key else 0)


def _is_key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)

# Loopback only: the AVD reaches this over its host-loopback alias
# (10.0.2.2), which is routed to the host's own 127.0.0.1 regardless of
# which local address this actually binds -- there's no reason for this to
# be reachable from the LAN. The protocol has no authentication at all
# (anything that can open the socket can make this launch an arbitrary
# configured emulator, or hand an arbitrary app_id to Steam's URI handler),
# so binding 0.0.0.0 would expose the bridge unnecessarily.
HOST = "127.0.0.1"

INTENT_CMP_RE = re.compile(r"cmp=(\S+)")
INTENT_DAT_RE = re.compile(r"dat=(\S+)")

# iiSU's own default emulator list routes Steam/GOG/Epic-style entries to
# GameNative, an Android app that runs Windows PC games under Wine/Box64 --
# irrelevant here, since this project always has the real thing (Steam
# itself) available PC-side already. Its launch command
# (emuladores_default.json's "%PACKAGE%/.MainActivity -a
# app.gamenative.LAUNCH_GAME -e app_id %GAMENATIVE_APP_ID_INT%") passes the
# Steam App ID as a plain int Intent extra, not a file path or ClipData URI
# -- there's no "rom" to find under roms_dir at all, unlike every other
# entry in config.json's emulators map, so this is handled as its own
# special case below rather than forced into the exe_names/rom_path shape
# every other profile uses.
GAMENATIVE_PACKAGE = "app.gamenative"
WINDOWS_LAUNCH_PACKAGES = {"com.winlator.cmod", "com.winlator", "com.cmodded.winlator"}

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
            creationflags=0x08000000,  # CREATE_NO_WINDOW
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
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _adb_media_volume_get() -> int | None:
    """Read Android's MUSIC stream volume (stream 3) without showing a console."""
    try:
        result = subprocess.run(
            ["adb", "shell", "media", "volume", "--stream", "3", "--get"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # Typical Android output: "volume is 7 in range [0..15]"
    match = re.search(r"volume\s+is\s+(\d+)", result.stdout, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _adb_media_volume_set(volume: int) -> bool:
    try:
        result = subprocess.run(
            ["adb", "shell", "media", "volume", "--stream", "3", "--set", str(max(0, volume))],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


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


def end_game_handoff() -> None:
    """Restore iiSU controller navigation and its previous Android volume."""
    debug_log("end_game_handoff() entered")
    global game_handoff_active, saved_android_media_volume, pending_native_quit
    previous = saved_android_media_volume
    saved_android_media_volume = None
    if previous is not None:
        _adb_media_volume_set(previous)
    with current_process_lock:
        game_handoff_active = False
        pending_native_quit = False
    print("[bridge] iiSU input/audio restored")


def load_path_cache() -> dict:
    if not PATH_CACHE_PATH.is_file():
        return {"executables": {}, "roms": {}}
    try:
        cache = json.loads(PATH_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"executables": {}, "roms": {}}
    cache.setdefault("executables", {})
    cache.setdefault("roms", {})
    return cache


def save_path_cache(cache: dict) -> None:
    try:
        PATH_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def log_launch(line: str, notify: bool = False, notify_title: str = "Community-iiSU-PC") -> None:
    """Appends one line to launch_history.log with a timestamp -- every
    launch attempt gets logged here regardless of outcome, not just
    failures, so there's always a record to check against ("did this
    actually try to launch, and with what") rather than only ever finding
    out about a problem after the fact with nothing to look back on.
    notify additionally raises a tray balloon (see boot_overlay.
    notify_error) for anything worth interrupting someone over -- a
    failure, not a routine successful launch -- since the bridge runs
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


EXECUTABLE_SEARCH_MAX_DEPTH = 4
ROM_SEARCH_MAX_DEPTH = 3


def _find_by_name(root: Path, names: set[str], max_depth: int) -> Path | None:
    """Breadth-first search for any file in `names` under root, capped at
    max_depth directory levels below root (root's direct children are
    depth 0). Replaces Path.rglob(), whose unbounded recursion means one
    slow, unavoidable round-trip per folder for every folder in the
    *entire* tree when root lives on a network share (search_roots and
    roms_dir are both commonly a Windows-mapped X:\\ drive here) --
    exactly the "takes forever" scan this exists to fix. Emulator installs
    and this project's curated ROM folders are never more than a few
    levels deep, so a modest cap avoids wandering into irrelevant,
    deeply-nested subfolders an rglob can't tell apart from a real match
    ahead of time -- an emulator's own save states/BIOS/thumbnails/cache
    dirs, or (worse, since it recurses *into* it for no reason) a
    multi-gigabyte Xbox 360 title's own .data folder sitting right next to
    the file actually being searched for. BFS also means a shallower
    match is always returned over a deeper coincidental same-name file,
    which rglob's traversal order doesn't guarantee at all."""
    current = [root]
    depth = 0
    while current and depth <= max_depth:
        next_level = []
        for directory in current:
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                if entry.name in names and entry.is_file():
                    return Path(entry.path)
            for entry in entries:
                if entry.is_dir():
                    next_level.append(Path(entry.path))
        current = next_level
        depth += 1
    return None


def find_executable(names: list[str], search_roots: list[Path], cache: dict) -> Path | None:
    """Scanning search_roots (which commonly include all of C:/Program
    Files) on every single launch is real, avoidable latency -- the result
    almost never changes between launches, so it's cached by exe name and
    only re-scanned if the cached path stops existing (e.g. the emulator
    got moved/reinstalled elsewhere).

    The cache key includes search_roots itself (not just the exe names),
    so editing search_roots in manager.py naturally invalidates the old
    entry instead of it staying wrong until the previously-found file
    happens to disappear -- manager.py documents that path changes apply
    on the very next launch with no restart needed, and a stale cache hit
    would quietly break that."""
    cache_key = "|".join(names) + "::" + "|".join(str(r) for r in search_roots)
    cached = cache["executables"].get(cache_key)
    if cached and Path(cached).is_file():
        return Path(cached)

    for root in search_roots:
        if not root.is_dir():
            continue
        match = _find_by_name(root, set(names), EXECUTABLE_SEARCH_MAX_DEPTH)
        if match:
            cache["executables"][cache_key] = str(match)
            return match
    cache["executables"].pop(cache_key, None)
    return None


def find_rom(rom_filename: str, roms_dir: Path, cache: dict) -> Path | None:
    """Same caching approach as find_executable, keyed on roms_dir too so
    changing it in manager.py doesn't risk returning a stale path."""
    cache_key = f"{rom_filename}::{roms_dir}"
    cached = cache["roms"].get(cache_key)
    if cached and Path(cached).is_file():
        return Path(cached)

    if not roms_dir.is_dir():
        return None
    match = _find_by_name(roms_dir, {rom_filename}, ROM_SEARCH_MAX_DEPTH)
    if match:
        cache["roms"][cache_key] = str(match)
        return match
    cache["roms"].pop(cache_key, None)
    return None


def find_emulator_for_package(package: str, emulators: dict, rom_filename: str | None, android_core: str | None) -> dict | None:
    """Every package here (RetroArch aside) is single-system, so the
    package match alone already tells the whole story -- no guessing from
    the ROM's extension involved, or needed, for any of those.

    RetroArch (com.retroarch) is the one exception: it's a multi-core,
    multi-console frontend on the Android side, and iiSU reports that same
    package for it regardless of which system the game actually is. But
    iiSU also always reports *which core it actually launched with* --
    the intent's LIBRETRO extra (android_core) -- and that's a strictly
    better signal than the ROM's file extension, which can be outright
    wrong: .chd is chdman's container for both PS1 CDs and Dreamcast GD-
    ROMs, and plenty of consoles here share .zip/.7z, so guessing the
    console from extension alone is guessing at something android_core
    already just told us. android_core is trusted first, unconditionally,
    whenever it's present -- not just for a curated subset of extensions --
    which also means any core this project has never explicitly curated
    (e.g. an arcade/MAME core resolved for a .zip) still gets routed
    correctly instead of falling through to a guess.

    A resolved core first checks RETROARCH_CORE_OVERRIDES -- cores with
    their own dedicated, better-suited standalone emulator PC-side (e.g.
    Flycast over RetroArch-with-flycast-core for Dreamcast) -- confirmed
    live as necessary: a Dreamcast .gdi launched com.retroarch with
    LIBRETRO=flycast_libretro_android.so even with standalone Flycast
    picked in iiSU. Failing that override, the core is passed straight
    through to a generic RetroArch launch (-L cores/<core>.dll) -- so
    long as that core is actually present on the Windows RetroArch
    install (launch_bridge.ensure_retroarch_core downloads it from the
    libretro buildbot if it isn't) -- rather than substituted into one of
    this module's own curated per-extension templates, so this isn't
    limited to consoles/cores someone has explicitly added here.

    Only when android_core is missing entirely (or doesn't look like a
    libretro-android core filename at all) does this fall back to the
    plain extension-based guess in RETROARCH_BY_EXTENSION/
    RETROARCH_SAFETY_NET_EXTENSIONS -- a reasonable default for the rare
    case iiSU doesn't report a core, never the primary mechanism."""
    for prefix, profile in emulators.items():
        if not package.startswith(prefix):
            continue
        if "by_extension" in profile:
            by_ext = profile["by_extension"]

            if android_core:
                core_dll = retroarch_core_dll_for_android_core(android_core)
                if core_dll:
                    override = standalone_profile_for_core_dll(core_dll)
                    if override:
                        return override
                    return {"exe_names": ["retroarch.exe"], "pre_args": ["-L", f"cores/{core_dll}", "-f"]}

            ext = Path(rom_filename).suffix.lower() if rom_filename else None
            return by_ext.get(ext) if ext else None
        return profile
    return None


def core_dll_from_pre_args(pre_args: list[str]) -> str | None:
    for arg in pre_args:
        if arg.startswith("cores/") or arg.startswith("cores\\"):
            return Path(arg).name
    return None


def ensure_retroarch_core(retroarch_dir: Path, core_dll: str) -> bool:
    """RetroArch loads its core list from disk, not from anything iiSU or
    this bridge tracks -- a core this project's own config.json expects
    (e.g. fceumm_libretro.dll for NES) can easily not actually be there if
    it was never downloaded through RetroArch's own Online Updater. RetroArch
    doesn't error visibly when that happens: it just fails to load the core
    and exits straight back to iiSU, which from the PC side looks
    indistinguishable from nothing happening at all. Downloads the missing
    core from libretro's own official nightly buildbot (the same binaries
    RetroArch's in-app updater itself pulls from) instead of leaving that
    silent failure to happen. Returns True if the core is present by the
    time this returns (already there, or freshly downloaded), False if it
    couldn't be obtained -- the caller still attempts the launch either way,
    since a download failure here shouldn't be worse than today's silent
    RetroArch exit."""
    core_path = retroarch_dir / "cores" / core_dll
    if core_path.is_file():
        return True

    url = LIBRETRO_CORE_URL.format(core_dll=core_dll)
    print(f"[bridge] {core_dll} isn't installed -- downloading it from the libretro buildbot...")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            zip_bytes = resp.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            data = z.read(core_dll)
    except Exception as e:
        print(f"[bridge] couldn't download {core_dll} ({e}) -- the game launch will likely fail")
        return False

    core_path.parent.mkdir(parents=True, exist_ok=True)
    core_path.write_bytes(data)
    print(f"[bridge] installed {core_dll}")
    return True


def friendly_emulator_name(executable: Path) -> str:
    """Human-readable name for the handoff overlay's status line (e.g.
    "Waiting on DuckStation..." instead of "Waiting on duckstation-qt-x64-
    ReleaseLTCG.exe..."). Falls back to the executable's own filename --
    still readable, just less polished -- for anything not in this
    project's curated list, e.g. some other RetroArch-compatible fork."""
    for app_label, exe_names in all_emulator_exe_names():
        if executable.name in exe_names:
            return app_label
    return executable.stem


def launch_iisu(config: dict) -> None:
    """Starts iiSU's own main activity directly via adb, instead of leaving
    the stock Android home screen showing after boot. iiSU declares both
    LAUNCHER and HOME categories on this activity (it's designed to be a
    home-screen replacement), but isn't necessarily set as this AVD's
    default home app, so this just launches it directly rather than
    depending on that.

    Retries every second since `am start` can fail with a transient "does
    not exist" error for a while right after a cold boot -- package
    manager can still be resolving components even once Android's own
    home screen is already visible (confirmed via `dumpsys package`: the
    activity is genuinely registered, `am start` just tried too early).

    Deliberately does NOT gate this on sys.boot_completed first (setup_
    wizard.py's own separate wait_for_avd() uses that signal, but only for
    the one-time install boot): boot_completed only flips once *every*
    system app's BOOT_COMPLETED receiver has finished, which is well
    after Android's own home screen is already interactive -- gating the
    first am start attempt on that turned "wait however long it takes for
    am start to actually succeed" (this loop
    alone) into "wait for full boot_completed first, THEN start trying,"
    adding a real, needless delay confirmed live (iiSU sitting on the
    stock home screen for 1-2 minutes) instead of shortening one."""
    component = config.get("iisu_component", DEFAULT_IISU_COMPONENT)
    result = None
    for _ in range(60):
        result = subprocess.run(["adb", "shell", "am", "start", "-n", component], capture_output=True, text=True)
        if result.returncode == 0 and "Error" not in result.stdout:
            set_volume_max()
            return
        time.sleep(1)
    print(f"[bridge] could not launch iiSU ({component}):")
    if result is not None:
        print(f"    {result.stdout.strip()}\n    {result.stderr.strip()}")


def set_volume_max() -> None:
    """A fresh boot comes up at whatever media volume level the system
    image defaults to (usually well below max) -- silent enough that
    anything iiSU itself plays (UI sounds, trailers) needs a manual
    volume raise inside the VM on every single boot otherwise. Repeated
    VOLUME_UP keyevents clamp at the device's actual max regardless of
    AOSP vs OEM MAX_VOLUME differences, so this doesn't need to know the
    exact volume index -- one `adb shell input keyevent` call with the
    keycode repeated is enough, no need for 20 separate subprocess calls."""
    subprocess.run(["adb", "shell", "input", "keyevent"] + ["24"] * 20, capture_output=True, text=True)


def show_iisu_window(config: dict) -> None:
    """Brings the iiSU/AVD window to the foreground, borderless-fullscreen
    if "iisu_fullscreen" is set in config.json. Plain SW_MAXIMIZE doesn't
    work on this window (it clamps its own max size), so true fullscreen
    means stripping the title bar/border and resizing to the screen -- see
    winapi.make_fullscreen."""
    end_game_handoff()
    hwnd = find_window_by_title(config["iisu_window_title"])
    if hwnd is None:
        print("[bridge] could not locate iiSU window")
        return
    force_foreground(hwnd, SW_RESTORE)
    if config.get("iisu_fullscreen"):
        make_fullscreen(hwnd)
        hide_emulator_toolbar()


def bring_emulator_to_foreground(pid: int) -> None:
    """Poll for the new process's main window (it takes a moment to appear
    after Popen returns) and force it to the foreground once found."""
    hwnd = wait_for_window_by_pid(pid)
    if hwnd is None:
        print("[bridge] could not locate emulator window to focus")
        return
    force_foreground(hwnd)
    # Give the window a moment to finish becoming active before clicking it.
    time.sleep(0.3)
    nudge_focus_with_click(hwnd)


def wait_and_restore_iisu(process: subprocess.Popen, config: dict, emulator_name: str) -> None:
    """Runs on a background thread: waits for the emulator to close (whether
    normally or via the quit hotkey), then un-hides and refocuses the iiSU
    AVD window, mirroring how the real Android launcher reappears once a
    game exits.

    A non-zero exit code is only a heuristic for "this launch actually
    failed," not a certainty -- some emulators exit non-zero on a normal
    quit too -- but it's the only signal available for the class of
    failure that shows its own error dialog and waits for it to be
    dismissed rather than crashing outright (confirmed live: RPCS3's
    missing-boot-target dialog, DuckStation's missing-SBI-file dialog --
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
    rather than given a visible console -- a console window for a script
    that just prints a handful of status lines and exits is pure clutter
    -- unless config.json's "debug_show_console_windows" says otherwise."""
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
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            cwd=str(STOP_SCRIPT.parent),
        )
    else:
        stop_log_file = open(STOP_LOG_PATH, "wb")
        try:
            subprocess.Popen(
                [sys.executable, str(STOP_SCRIPT)],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
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

    quit_hotkey no longer goes through this at all -- see quit_key_watcher,
    which runs as its own separate thread/mechanism (GetAsyncKeyState
    polling, not RegisterHotKey) since it now needs to tell a quick tap
    apart from a multi-second hold, something RegisterHotKey's single
    fire-once-per-press model has no way to express. shutdown_hotkey stays
    on the old mechanism as a distinct, independent combo for anyone who
    wants one in addition to holding quit_hotkey -- it's optional (None
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


def quit_key_watcher(config: dict) -> None:
    global pending_native_quit
    """Polls quit_hotkey's key (default: Escape, no modifiers) via
    GetAsyncKeyState instead of RegisterHotKey, on its own thread.

    A tap's meaning depends on whether an emulator is actually running:
    with one running, it force-quits it and returns to iiSU (the original
    quit_hotkey behavior); with none running -- already sitting at iiSU
    itself -- there's nothing to "quit back to", so it closes iiSU and the
    AVD entirely instead. This replaced tapping being a no-op with nothing
    running (proc.terminate() guarded on proc not being None, so a tap did
    genuinely nothing, confirmed live -- reported as "pressing Escape
    doesn't close iiSU").

    Holding the key for shutdown_hold_seconds (default 5) also closes
    iiSU and the AVD entirely, *regardless* of whether an emulator is
    running -- kept as a way to skip straight to shutdown without
    quitting back to iiSU first, mid-game. RegisterHotKey can't express
    "how long has this been held," so this needed its own polling loop
    rather than reusing hotkey_listener's message-loop mechanism."""
    hotkey_config = config["quit_hotkey"]
    key_name = hotkey_config.get("key", "escape")
    vk = resolve_vk(key_name)
    modifier_names = hotkey_config.get("modifiers", [])
    modifier_vks = [MODIFIER_VK[name] for name in modifier_names if name in MODIFIER_VK]
    hold_seconds = config.get("shutdown_hold_seconds", DEFAULT_SHUTDOWN_HOLD_SECONDS)
    label = "+".join([*modifier_names, key_name]).upper()

    print(
        f"[bridge] {label}: tap to quit the running emulator (or close iiSU entirely if none is running); "
        f"hold {hold_seconds}s to close iiSU and the AVD entirely regardless"
    )

    pressed_since: float | None = None
    shutdown_fired = False
    while True:
        time.sleep(KEY_POLL_INTERVAL)
        is_down = _is_key_down(vk) and all(_is_key_down(m) for m in modifier_vks)
        if is_down:
            if pressed_since is None:
                pressed_since = time.monotonic()
                shutdown_fired = False
                debug_log(f"quit hotkey DOWN: {label}")
            elif not shutdown_fired and time.monotonic() - pressed_since >= hold_seconds:
                shutdown_fired = True
                print(f"[bridge] {label} held {hold_seconds}s -- closing iiSU and the AVD entirely...")
                shutdown_everything()
        else:
            if pressed_since is not None:
                debug_log(f"quit hotkey UP: {label}; shutdown_fired={shutdown_fired}")
            if pressed_since is not None and not shutdown_fired:
                with current_process_lock:
                    proc = current_process
                    native_hwnd = current_native_window
                    native_pid = current_native_pid
                    handoff_active = game_handoff_active
                if native_hwnd is not None and user32.IsWindow(native_hwnd):
                    print(f"[bridge] {label} tapped, quitting native game")
                    log_launch(
                        f"QUIT: {label} tapped -- native HWND {native_hwnd}, "
                        f"PID {native_pid}"
                    )
                    if native_pid and _pid_is_running(native_pid):
                        # Once a native game has been identified, its owning PID
                        # is authoritative. current_process may only be a launcher
                        # or wrapper executable that remains alive beside it.
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
                    print(
                        f"[bridge] {label} tapped, native game window changed -- "
                        f"terminating tracked PID {native_pid}"
                    )
                    log_launch(
                        f"QUIT: {label} tapped -- native HWND changed; "
                        f"terminating PID {native_pid}"
                    )
                    _terminate_native_pid(native_pid)
                    if proc is not None and proc.poll() is None and proc.pid != native_pid:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                elif proc is not None and proc.poll() is None:
                    # No native HWND/PID has been established, so this is still
                    # a normal subprocess-backed emulator/game launch.
                    print(f"[bridge] {label} tapped, terminating emulator")
                    proc.terminate()
                elif handoff_active:
                    # Steam may still be between URI invocation and creation of
                    # the game's real window. Queue the quit instead of either
                    # shutting down iiSU-PC or silently dropping the command.
                    #
                    # IMPORTANT: do not acquire current_process_lock here.
                    # quit_key_watcher already sampled the handoff state under
                    # that lock above; the previous version attempted to take
                    # the same non-reentrant Lock a second time and could
                    # permanently deadlock the quit-watcher thread.
                    pending_native_quit = True
                    print(f"[bridge] {label} tapped during native launch handoff -- quit queued")
                    log_launch(f"QUIT: {label} tapped during native launch handoff; queued until HWND/PID is tracked")
                else:
                    print(f"[bridge] {label} tapped with no emulator/native game running -- closing iiSU and the AVD entirely...")
                    log_launch(f"QUIT: {label} tapped -- no tracked emulator/native HWND; shutting down iiSU-PC")
                    shutdown_everything()
            pressed_since = None



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
    show_overlay = not config.get("debug_show_console_windows", False)
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
            log_launch(f"LAUNCHED: Windows app {app_name} -- URI {uri}")
            os.startfile(uri)
        else:
            args = [str(executable), *configured_args]
            print(f"[bridge] launching Windows app: {args}")
            log_launch(f"LAUNCHED: Windows app {app_name} -- {args}")
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
        log_launch(f"TRACKING: Windows app {app_name} -- HWND {hwnd}, PID {native_pid}")
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
    show_overlay = not config.get("debug_show_console_windows", False)
    overlay = boot_overlay.show(f"Waiting on Steam app {app_id}...") if show_overlay else None
    hwnd = None

    try:
        iisu_hwnd = find_window_by_title(config["iisu_window_title"])
        if iisu_hwnd is not None:
            user32.ShowWindow(iisu_hwnd, SW_MINIMIZE)
        else:
            print("[bridge] could not locate iiSU window to hide")

        print(f"[bridge] launching Steam app {app_id}...")
        os.startfile(f"steam://rungameid/{app_id}")

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
            f"TRACKING: Steam app {app_id} via GameNative -- HWND {hwnd}, PID {native_pid}"
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


def handle_request(raw_intent: str) -> None:
    print(f"[bridge] received: {raw_intent}")

    # Reloaded fresh per request (not once at startup) so edits made in
    # manager.py take effect on the very next launch without restarting
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

            if not launch_windows_app(app_name, config):
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
    # of a data URI/ClipData -- iiSU launches RetroArch exclusively this
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
            log_launch("FAILED: GameNative launch with no app_id extra -- can't tell Steam what to run", notify=True)
            return
        log_launch(f"LAUNCHED: Steam app {app_id} via GameNative")
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
        # Already a plain filesystem path, not URI-encoded -- no unquote().
        rom_filename = extras["ROM"].rsplit("/", 1)[-1]
    else:
        rom_filename = None

    profile = find_emulator_for_package(package, config["emulators"], rom_filename, extras.get("LIBRETRO"))
    if profile is None:
        log_launch(f"FAILED: no known PC emulator mapped for package '{package}' (rom '{rom_filename}')", notify=True)
        return

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

    # Every emulator here except RPCS3 takes its rom as a trailing
    # positional argument after any flags -- RPCS3's own CLI is the
    # opposite (confirmed against its actual usage,
    # "rpcs3.exe <game_path> --no-gui --fullscreen"): the boot target has
    # to come *before* --no-gui/--fullscreen, or it parses as neither
    # flag having a boot target at all ("Cannot run no-gui mode without
    # boot target" -- confirmed live). rom_before_args, when a profile
    # sets it, is the escape hatch for that rather than hardcoding RPCS3
    # as a special case here.
    if rom_path and profile.get("rom_before_args"):
        args = [str(executable), str(rom_path), *profile["pre_args"]]
    else:
        args = [str(executable), *profile["pre_args"]]
        if rom_path:
            args.append(str(rom_path))

    # Covers the gap between iiSU's window minimizing and the real PC
    # emulator's own window appearing and taking the foreground -- without
    # it, that moment shows raw desktop. Skipped when debug_show_console_
    # windows is on, since a fullscreen overlay would just hide the
    # console windows that setting exists to show.
    show_overlay = not config.get("debug_show_console_windows", False)
    overlay = boot_overlay.show(f"Waiting on {friendly_emulator_name(executable)}...") if show_overlay else None
    try:
        iisu_hwnd = find_window_by_title(config["iisu_window_title"])
        if iisu_hwnd is not None:
            user32.ShowWindow(iisu_hwnd, SW_MINIMIZE)
        else:
            print("[bridge] could not locate iiSU window to hide")

        print(f"[bridge] launching: {args}")
        log_launch(f"LAUNCHED: {friendly_emulator_name(executable)} -- {args}")
        process = subprocess.Popen(args, cwd=str(executable.parent))
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

        bring_emulator_to_foreground(process.pid)
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
    # wait_and_restore_iisu thread owns frontend restoration, so do not bring
    # iiSU over it merely because later request handling raised.
    if proc is not None and proc.poll() is None:
        debug_log(
            "request exception occurred while tracked subprocess is still "
            f"running; leaving iiSU suspended for PID={proc.pid}"
        )
        return

    debug_log(
        "request exception left no live tracked game; restoring iiSU "
        "and ending any incomplete handoff"
    )
    show_iisu_window(config)


def main() -> None:
    # Install bridge-specific crash logging only when this module is actually
    # running the bridge. manager.py imports helpers from launch_bridge and
    # must retain its own sys/threading exception hooks.
    sys.excepthook = _log_uncaught_exception
    if hasattr(threading, "excepthook"):
        threading.excepthook = _log_thread_exception

    debug_log("=" * 72)
    debug_log(
        f"launch_bridge starting; PID={os.getpid()}; Python={sys.version.split()[0]}"
    )

    try:
        config = load_config()
    except ConfigMissingError as e:
        print(f"[bridge] {e}")
        sys.exit(1)

    threading.Thread(target=hotkey_listener, args=(config,), daemon=True).start()
    threading.Thread(target=quit_key_watcher, args=(config,), daemon=True).start()

    threading.Thread(target=ControllerBridge(is_game_running, shutdown_everything).run, daemon=True).start()

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
        print(f"[bridge] listening on {HOST}:{config['bridge_port']}")
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
