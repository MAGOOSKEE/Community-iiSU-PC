"""
Cross-platform window-management dispatch: everything that calls this
module (launch_bridge.py, apply_display.py, the Manager) imports from
`winapi` exactly as before, on either platform, this file just decides
which real implementation backs it, ctypes-based Win32 calls
(winapi_windows.py) or KWin/xdotool (winapi_linux.py). Keeps every call
site unchanged rather than needing its own if-Windows-else-Linux branch,
the same reasoning boot_overlay_qt.py's docstring already gives for
keeping a stable API while its own internals changed.
"""

from shared.platform_compat import IS_WINDOWS

if IS_WINDOWS:
    from winapi_windows import *  # noqa: F401,F403
else:
    from winapi_linux import *  # noqa: F401,F403
