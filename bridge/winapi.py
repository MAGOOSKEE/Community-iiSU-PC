"""
Shared Win32 window-management helpers (ctypes, stdlib only) used by
launch_bridge.py, apply_display.py, and manager.py.

HWND is pointer-sized; without explicit argtypes/restype ctypes assumes
32-bit ints on some of these, which silently truncates handles on 64-bit
Windows and makes calls like SetForegroundWindow a no-op. Declaring them
properly here is what actually makes this work.
"""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
# LONG_PTR is pointer-sized (64-bit on x64 Windows); c_ssize_t matches that.
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

SW_HIDE = 0
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
HWND_TOP = 0
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020
SM_CXSCREEN = 0
SM_CYSCREEN = 1
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
WS_BORDERLESS_MASK = WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU


def _find_window(predicate) -> int | None:
    found = {"hwnd": None}

    def callback(hwnd, _lparam):
        if predicate(hwnd):
            found["hwnd"] = hwnd
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found["hwnd"]


def _window_title(hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def find_window_by_pid(pid: int) -> int | None:
    def matches(hwnd):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        return owner_pid.value == pid and user32.IsWindowVisible(hwnd)

    return _find_window(matches)


def find_window_by_title(substring: str) -> int | None:
    def matches(hwnd):
        return user32.IsWindowVisible(hwnd) and substring.lower() in _window_title(hwnd).lower()

    return _find_window(matches)


def find_window_by_exact_title(title: str) -> int | None:
    def matches(hwnd):
        return user32.IsWindowVisible(hwnd) and _window_title(hwnd) == title

    return _find_window(matches)


def hide_emulator_toolbar() -> None:
    """The standalone Android Emulator's side toolbar (power/volume/rotate/
    settings icons) is a separate top-level window titled just "Emulator"
    docked at the edge of the main device window -- not a panel inside it,
    and not something exposed via any emulator command-line flag or config.
    Since it's its own window, we can just hide it directly."""
    hwnd = find_window_by_exact_title("Emulator")
    if hwnd is not None:
        user32.ShowWindow(hwnd, SW_HIDE)


class _DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_uint16),
        ("dmDriverVersion", ctypes.c_uint16),
        ("dmSize", ctypes.c_uint16),
        ("dmDriverExtra", ctypes.c_uint16),
        ("dmFields", ctypes.c_uint32),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_uint32),
        ("dmDisplayFixedOutput", ctypes.c_uint32),
        ("dmColor", ctypes.c_int16),
        ("dmDuplex", ctypes.c_int16),
        ("dmYResolution", ctypes.c_int16),
        ("dmTTOption", ctypes.c_int16),
        ("dmCollate", ctypes.c_int16),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_uint16),
        ("dmBitsPerPel", ctypes.c_uint32),
        ("dmPelsWidth", ctypes.c_uint32),
        ("dmPelsHeight", ctypes.c_uint32),
        ("dmDisplayFlags", ctypes.c_uint32),
        ("dmDisplayFrequency", ctypes.c_uint32),
    ]


_ENUM_CURRENT_SETTINGS = -1


def get_primary_monitor_mode() -> tuple[int, int, int]:
    """Returns (width, height, refresh_hz) for the current primary monitor,
    so the setup GUI can offer to match the AVD's display profile to it
    instead of the user guessing values by hand.

    dmDisplayFrequency coming back as 0 or 1 doesn't mean the monitor
    actually runs at 0Hz or 1Hz -- per Microsoft's own documentation for
    this field, both values mean "the display hardware's default refresh
    rate," which some drivers report instead of the real number (seen in
    practice on at least one real machine). Falls back to a sane default
    in that case rather than handing a literal 0/1 Hz on to the AVD's
    display profile."""
    dm = _DEVMODE()
    dm.dmSize = ctypes.sizeof(_DEVMODE)
    user32.EnumDisplaySettingsW(None, _ENUM_CURRENT_SETTINGS, ctypes.byref(dm))
    refresh_hz = dm.dmDisplayFrequency if dm.dmDisplayFrequency > 1 else 60
    return dm.dmPelsWidth, dm.dmPelsHeight, refresh_hz


def force_foreground(hwnd: int, show_state: int = SW_RESTORE) -> None:
    """Windows normally blocks background processes from stealing focus;
    this uses the standard AttachThreadInput workaround to get around that."""
    current_thread_id = kernel32.GetCurrentThreadId()
    target_thread_id = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(target_thread_id, current_thread_id, True)
    user32.ShowWindow(hwnd, show_state)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    user32.AttachThreadInput(target_thread_id, current_thread_id, False)


def nudge_focus_with_click(hwnd: int) -> None:
    """SetForegroundWindow only makes the top-level window active; Qt apps
    like DuckStation track actual keyboard/controller input focus on their
    render widget separately, which only picks it up on a real click. This
    synthesizes that click at the window's center (moves the real cursor)."""
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    user32.SetCursorPos(cx, cy)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)


def make_fullscreen(hwnd: int) -> None:
    """True borderless fullscreen: SW_MAXIMIZE alone doesn't work on windows
    (like the Android Emulator's) that clamp their own max size, and simply
    resizing via SetWindowPos still leaves the title bar/border eating into
    the screen. This strips the caption/border styles first, then resizes
    to the full screen -- the standard "borderless fullscreen" technique."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~WS_BORDERLESS_MASK)

    width = user32.GetSystemMetrics(SM_CXSCREEN)
    height = user32.GetSystemMetrics(SM_CYSCREEN)
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, width, height, SWP_SHOWWINDOW | SWP_FRAMECHANGED)


def wait_for_window_by_pid(pid: int, timeout: float = 8.0) -> int | None:
    deadline = time.time() + timeout
    hwnd = find_window_by_pid(pid)
    while hwnd is None and time.time() < deadline:
        time.sleep(0.2)
        hwnd = find_window_by_pid(pid)
    return hwnd


def wait_for_window_by_title(substring: str, timeout: float = 60.0) -> int | None:
    deadline = time.time() + timeout
    hwnd = find_window_by_title(substring)
    while hwnd is None and time.time() < deadline:
        time.sleep(0.5)
        hwnd = find_window_by_title(substring)
    return hwnd
