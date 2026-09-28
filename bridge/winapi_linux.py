"""
Linux window-management helpers, the Linux half of winapi.py's cross-
platform dispatch (see that module; winapi_windows.py is the Windows
half). Used by launch_bridge.py, apply_display.py, and the Manager.

Real, working behavior (ported from jacksterson's Linux port) for the
functions that matter most day to day: finding the emulator window,
making it fullscreen, hiding its side toolbar, bringing it to the
foreground, and reading the primary monitor's resolution. These use
KDE's KWin scripting interface (via `qdbus`) plus `xdotool` as a second,
more general mechanism; this only actually works on KDE Plasma, not
GNOME or other desktop environments, that's a real, known limitation,
not something this module tries to hide.

Everything else this module exports (the RegisterHotKey-style user32
mock, precise window enumeration by process) is silently inert here
rather than crashing, so importing and running on Linux doesn't fail
outright, but those specific low-level pieces don't do anything real on
Linux. This is NOT a functional gap for the keyboard quit hotkey
itself, though: launch_bridge.py's _linux_key_watcher() implements that
feature completely separately, reading raw /dev/input events directly
rather than going through winapi's user32 dispatch at all (there's no
Linux equivalent of RegisterHotKey/GetMessageW to dispatch to). The
controller-based quit chord (evdev, controller_bridge.py) is a third,
independent mechanism, unaffected by any of this.
"""

import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SW_HIDE = 0
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9


class _InertWin32Func:
    """Stands in for a user32/kernel32 function on Linux: callable (so
    launch_bridge.py's direct user32.GetAsyncKeyState()-style calls don't
    raise) and its own argtypes/restype are plain mutable attributes (so
    the same module-level `user32.Foo.argtypes = [...]` ctypes setup
    launch_bridge.py does still works, it just declares types on an inert
    stand-in instead of a real Win32 function)."""

    def __init__(self, name: str = ""):
        self.argtypes: list = []
        self.restype = None
        self._name = name

    def __call__(self, *args, **kwargs):
        return 0


class _InertWin32Module:
    def __getattr__(self, name: str) -> _InertWin32Func:
        func = _InertWin32Func(name)
        setattr(self, name, func)
        return func


user32 = _InertWin32Module()
kernel32 = _InertWin32Module()


def _run_kwin_script(code: str, name: str = "iisu_kwin") -> bool:
    if not shutil.which("qdbus"):
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(code)
        script_path = f.name
    try:
        loaded = subprocess.run(
            ["qdbus", "org.kde.KWin", "/Scripting", "loadScript", script_path, name],
            capture_output=True, text=True, timeout=2,
        )
        if loaded.returncode != 0:
            return False
        subprocess.run(["qdbus", "org.kde.KWin", "/Scripting", "start"], capture_output=True, timeout=2)
        subprocess.run(["qdbus", "org.kde.KWin", "/Scripting", "unloadScript", name], capture_output=True, timeout=2)
        return True
    except Exception:
        return False
    finally:
        try:
            Path(script_path).unlink()
        except OSError:
            pass


def ensure_linux_kwin_rules() -> bool:
    """Configures KDE KWin window rules so the Android emulator window
    maps directly into borderless fullscreen and its auxiliary toolbar
    window is suppressed from creation, avoiding a border/toolbar flicker
    each launch. A no-op (returns False) without qdbus, i.e. off KDE."""
    if not shutil.which("qdbus"):
        return False
    kwin_rules_path = Path.home() / ".config" / "kwinrulesrc"
    try:
        content = kwin_rules_path.read_text(encoding="utf-8") if kwin_rules_path.exists() else ""
    except OSError:
        return False

    main_rule_id = str(uuid.uuid4())
    toolbar_rule_id = str(uuid.uuid4())
    toolbar_rule_id2 = str(uuid.uuid4())

    main_block = f"""
[{main_rule_id}]
Description=Community-iiSU-PC Main Window
fullscreen=true
fullscreenrule=3
noborder=true
noborderrule=3
closeable=true
closeablerule=2
title=Android Emulator
titlematch=2
types=1
wmclass=Emulator
wmclassmatch=1
"""
    toolbar_block = f"""
[{toolbar_rule_id}]
Description=Community-iiSU-PC Toolbar
minimize=true
minimizerule=2
opacityactive=0
opacityactiverule=2
opacityinactive=0
opacityinactiverule=2
skiptaskbar=true
skiptaskbarrule=2
skipswitcher=true
skipswitcherrule=2
types=4
typesrule=2
wmclass=Emulator
wmclassmatch=1

[{toolbar_rule_id2}]
Description=Community-iiSU-PC Toolbar Empty Title
minimize=true
minimizerule=2
opacityactive=0
opacityactiverule=2
opacityinactive=0
opacityinactiverule=2
skiptaskbar=true
skiptaskbarrule=2
skipswitcher=true
skipswitcherrule=2
title=
titlematch=1
wmclass=Emulator
wmclassmatch=1
"""
    # Strip any rule blocks a previous run of this same function added,
    # identified by the "Community-iiSU-PC" marker in their Description=,
    # so re-running this doesn't pile up duplicate rules over time.
    clean_lines: list[str] = []
    skip_section = False
    for line in content.splitlines():
        if line.strip().startswith("[") and line.strip().endswith("]"):
            skip_section = False
        if "Community-iiSU-PC" in line:
            skip_section = True
            if clean_lines and clean_lines[-1].strip().startswith("[") and clean_lines[-1].strip().endswith("]"):
                clean_lines.pop()
            continue
        if skip_section:
            continue
        clean_lines.append(line)

    existing_rules: list[str] = []
    for line in clean_lines:
        if line.startswith("rules="):
            existing_rules = [r.strip() for r in line.split("=", 1)[1].split(",") if r.strip()]
    existing_rules.extend([main_rule_id, toolbar_rule_id, toolbar_rule_id2])

    new_lines = []
    for line in clean_lines:
        if line.startswith("count="):
            new_lines.append(f"count={len(existing_rules)}")
        elif line.startswith("rules="):
            new_lines.append(f"rules={','.join(existing_rules)}")
        else:
            new_lines.append(line)
    new_content = "\n".join(new_lines).rstrip() + "\n" + main_block + toolbar_block + "\n"
    try:
        kwin_rules_path.parent.mkdir(parents=True, exist_ok=True)
        kwin_rules_path.write_text(new_content, encoding="utf-8")
        subprocess.run(["qdbus", "org.kde.KWin", "/KWin", "reconfigure"], capture_output=True, timeout=2)
        return True
    except OSError:
        return False


def _xdotool_search(*names: str) -> list[str]:
    if not shutil.which("xdotool"):
        return []
    ids: list[str] = []
    for name in names:
        try:
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", name],
                capture_output=True, text=True, timeout=2,
            )
        except Exception:
            continue
        if result.returncode == 0:
            ids.extend(line.strip() for line in result.stdout.splitlines() if line.strip())
    return ids


def _find_window(substring: str) -> int | None:
    for window_id in _xdotool_search(substring, "Android Emulator", "iisuwin", "qemu"):
        try:
            return int(window_id)
        except ValueError:
            continue

    # Last resort: confirm a real emulator/qemu process is actually
    # running (still not a real window handle, callers only ever use
    # this as a truthy "something's up" signal, never pass it back into
    # an xdotool/KWin call expecting a real window id). Deliberately NOT
    # "is qdbus/KWin reachable at all", which earlier just checked qdbus
    # could talk to KWin and returned a fake id=1 unconditionally,
    # including when no emulator was running at all.
    try:
        result = subprocess.run(
            ["pgrep", "-f", "qemu-system|[/ ]emulator( |$)"], capture_output=True, text=True, timeout=1,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line.isdigit():
                    continue
                pid = int(line)
                # pgrep -f matches against the FULL command line, including
                # its own argv (which contains this same search text),
                # confirmed live on a real system: without this check,
                # _find_window() always "finds" a window even when nothing
                # is running at all, since pgrep matches itself every time.
                try:
                    if Path(f"/proc/{pid}/comm").read_text().strip() == "pgrep":
                        continue
                except OSError:
                    pass
                return pid
    except Exception:
        pass
    return None


_FULLSCREEN_KWIN_SCRIPT = """
var clients = workspace.windowList();
for (var i = 0; i < clients.length; i++) {
    var c = clients[i];
    var cap = (c.caption || '').toLowerCase();
    var cls = (c.resourceClass || '').toLowerCase();
    if (cls === 'emulator' || cls.indexOf('qemu') !== -1) {
        if (c.normalWindow && (cap.indexOf('iisuwin') !== -1 || cap.indexOf('android emulator') !== -1)) {
            c.fullScreen = true;
            c.noBorder = true;
            workspace.activeWindow = c;
        } else {
            c.minimized = true;
            c.skipTaskbar = true;
            c.skipSwitcher = true;
            c.opacity = 0;
        }
    }
}
"""

_FOREGROUND_KWIN_SCRIPT = """
var clients = workspace.windowList();
for (var i = 0; i < clients.length; i++) {
    var c = clients[i];
    var cap = (c.caption || '').toLowerCase();
    var cls = (c.resourceClass || '').toLowerCase();
    if (cap.indexOf('iisuwin') !== -1 || cap.indexOf('android emulator') !== -1 || cls.indexOf('qemu') !== -1 || cls.indexOf('emulator') !== -1) {
        workspace.activeWindow = c;
    }
}
"""

_HIDE_TOOLBAR_KWIN_SCRIPT = """
var clients = workspace.windowList();
for (var i = 0; i < clients.length; i++) {
    var c = clients[i];
    var cap = (c.caption || '').toLowerCase();
    var cls = (c.resourceClass || '').toLowerCase();
    if (cls === 'emulator' || cls.indexOf('qemu') !== -1) {
        if (!c.normalWindow || cap.indexOf('android emulator') === -1) {
            c.minimized = true;
            c.skipTaskbar = true;
            c.skipSwitcher = true;
            c.opacity = 0;
        }
    }
}
"""


def hide_emulator_toolbar() -> None:
    _run_kwin_script(_HIDE_TOOLBAR_KWIN_SCRIPT, "iisu_hide_toolbar")
    _hide_toolbar_xdotool()


def _hide_toolbar_xdotool() -> None:
    if not shutil.which("xdotool"):
        return
    try:
        result = subprocess.run(["xdotool", "search", "--class", "Emulator"], capture_output=True, text=True, timeout=2)
    except Exception:
        return
    if result.returncode != 0 or not result.stdout.strip():
        return
    for window_id in result.stdout.splitlines():
        window_id = window_id.strip()
        if not window_id:
            continue
        try:
            name_result = subprocess.run(["xdotool", "getwindowname", window_id], capture_output=True, text=True, timeout=1)
        except Exception:
            continue
        name = name_result.stdout.strip()
        if "Android Emulator" not in name and "iisuwin" not in name:
            subprocess.run(["xdotool", "windowunmap", window_id], capture_output=True, timeout=1)
            subprocess.run(["xdotool", "windowminimize", window_id], capture_output=True, timeout=1)


def get_primary_monitor_mode() -> tuple[int, int, int]:
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                geometry = screen.geometry()
                refresh_hz = int(screen.refreshRate()) or 60
                return geometry.width(), geometry.height(), refresh_hz
    except Exception:
        pass
    return 1920, 1080, 60


def find_window_by_title(substring: str) -> int | None:
    return _find_window(substring)


def find_window_by_exact_title(title: str) -> int | None:
    return _find_window(title)


def force_foreground(hwnd: int = 0, show_state: int = SW_RESTORE) -> None:
    _run_kwin_script(_FOREGROUND_KWIN_SCRIPT, "iisu_force_foreground")
    if not shutil.which("xdotool"):
        return
    try:
        if hwnd and hwnd > 1:
            subprocess.run(["xdotool", "windowactivate", str(hwnd)], capture_output=True, timeout=1)
        else:
            for name in ("iisuwin", "Android Emulator"):
                subprocess.run(["xdotool", "search", "--name", name, "windowactivate", "%@"], capture_output=True, timeout=1)
    except Exception:
        pass


def make_fullscreen(hwnd: int = 0) -> None:
    ensure_linux_kwin_rules()
    _run_kwin_script(_FULLSCREEN_KWIN_SCRIPT, "iisu_make_fullscreen")
    if shutil.which("xdotool"):
        try:
            for name in ("iisuwin", "Android Emulator"):
                result = subprocess.run(["xdotool", "search", "--name", name], capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    for window_id in result.stdout.splitlines():
                        window_id = window_id.strip()
                        if window_id:
                            subprocess.run(["xdotool", "windowactivate", window_id], capture_output=True, timeout=1)
                            subprocess.run(["xdotool", "windowstate", "--add", "FULLSCREEN", window_id], capture_output=True, timeout=1)
        except Exception:
            pass
    hide_emulator_toolbar()
    try:
        subprocess.run(
            ["adb", "shell", "settings", "put", "global", "policy_control", "immersive.full=*"],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass


def nudge_focus_with_click(hwnd: int) -> None:
    # No general, DE-independent equivalent of synthesizing a click at a
    # specific window's center; not implemented here.
    return


def minimize_own_console() -> None:
    # Windows-only concept (pythonw.exe vs python.exe's own console
    # window); nothing to do on Linux.
    return


def list_visible_windows() -> set[int]:
    if not shutil.which("xdotool"):
        return set()
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "."], capture_output=True, text=True, timeout=2
        )
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    ids = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            ids.add(int(line))
    return ids


def window_process_name(hwnd: int) -> str:
    if not shutil.which("xdotool"):
        return ""
    try:
        pid_result = subprocess.run(["xdotool", "getwindowpid", str(hwnd)], capture_output=True, text=True, timeout=1)
        pid = pid_result.stdout.strip()
        if not pid:
            return ""
        comm_path = Path(f"/proc/{pid}/comm")
        return comm_path.read_text(encoding="utf-8").strip() if comm_path.is_file() else ""
    except Exception:
        return ""


def wait_for_new_visible_window(existing: set[int], timeout: float = 10.0) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = list_visible_windows() - existing
        if current:
            return next(iter(current))
        time.sleep(0.2)
    return None


def wait_for_new_visible_window_excluding_processes(
    existing: set[int], excluded_process_names: set[str], timeout: float = 10.0
) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd in list_visible_windows() - existing:
            if window_process_name(hwnd).lower() not in excluded_process_names:
                return hwnd
        time.sleep(0.2)
    return None


def wait_for_stable_new_visible_window(
    existing: set[int], timeout: float = 10.0, stable_for: float = 0.5
) -> int | None:
    hwnd = wait_for_new_visible_window(existing, timeout)
    if hwnd is not None:
        time.sleep(stable_for)
    return hwnd


def close_window(hwnd: int) -> bool:
    if not shutil.which("xdotool"):
        return False
    try:
        result = subprocess.run(["xdotool", "windowclose", str(hwnd)], capture_output=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


def wait_for_window_to_close(hwnd: int, timeout: float = 10.0) -> bool:
    if not shutil.which("xdotool"):
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = subprocess.run(["xdotool", "getwindowname", str(hwnd)], capture_output=True, timeout=1)
        except Exception:
            return True
        if result.returncode != 0:
            return True
        time.sleep(0.2)
    return False


def find_window_by_pid(pid: int) -> int | None:
    if not shutil.which("xdotool"):
        return None
    try:
        result = subprocess.run(["xdotool", "search", "--pid", str(pid)], capture_output=True, text=True, timeout=2)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def wait_for_visible_window_by_pid(pid: int, timeout: float = 8.0) -> int | None:
    deadline = time.time() + timeout
    hwnd = find_window_by_pid(pid)
    while hwnd is None and time.time() < deadline:
        time.sleep(0.2)
        hwnd = find_window_by_pid(pid)
    return hwnd


def wait_for_window_by_pid(pid: int, timeout: float = 8.0) -> int | None:
    return wait_for_visible_window_by_pid(pid, timeout)


def wait_for_window_by_title(substring: str, timeout: float = 60.0) -> int | None:
    deadline = time.time() + timeout
    hwnd = find_window_by_title(substring)
    while hwnd is None and time.time() < deadline:
        time.sleep(0.5)
        hwnd = find_window_by_title(substring)
    return hwnd
