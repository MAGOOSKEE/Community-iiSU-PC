"""Cross-platform helpers for running Community-iiSU-PC on Linux (KDE
Plasma) alongside its native Windows behavior.

Windows-only subprocess.Popen/run kwargs (creationflags) don't exist on
POSIX at all, subprocess itself raises ValueError if creationflags is
passed on a non-Windows platform, it isn't just silently ignored. Rather
than monkeypatching subprocess.Popen or faking a winreg module into
sys.modules to paper over that everywhere at once, callers explicitly
ask this module for the right value for the current platform, one
subprocess call at a time, the same explicit style jre_env.py already
uses for choosing a Java runtime.
"""

import os
import sys

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def open_uri(uri: str) -> None:
    """Opens a URI (a registered protocol like steam://..., or a plain
    file/folder path) with whatever the OS has associated with it.
    os.startfile() doesn't exist on Linux at all (AttributeError, not a
    no-op), the equivalent there is xdg-open, which reads the same kind
    of registered-handler association (a .desktop file declaring
    MimeType=x-scheme-handler/steam;, for the steam:// case) via
    xdg-mime/mimeapps.list."""
    if IS_WINDOWS:
        os.startfile(uri)
        return
    import subprocess
    subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# CREATE_NO_WINDOW: only meaningful on Windows (suppresses a console-
# subsystem child's own window when launched from a GUI app with no
# console of its own). Kept as a plain int here, not read from the
# subprocess module, so importing this file never requires being on
# Windows.
CREATE_NO_WINDOW = 0x08000000


def subprocess_creationflags() -> int:
    """The creationflags value to pass a subprocess.run()/Popen() call
    that wants a console-subsystem child (adb, git, java, apktool, etc.)
    to not flash its own window. Returns CREATE_NO_WINDOW on Windows, or
    0 on Linux: subprocess.Popen raises ValueError for any *non-zero*
    creationflags on a non-Windows platform, it isn't silently ignored,
    so this must return the real default (0), not just an "ignore this"
    placeholder like None. A call site does
    `creationflags=subprocess_creationflags()` unconditionally on both
    platforms."""
    return CREATE_NO_WINDOW if IS_WINDOWS else 0


def detached_popen_kwargs() -> dict:
    """Extra subprocess.Popen() kwargs to start a background process that
    outlives this one and doesn't receive this process's own Ctrl+C/
    signals (the Manager spawning the bridge/stop scripts, or the AVD's
    emulator process). Windows achieves that with creation flags
    (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, plus CREATE_NO_WINDOW to
    also suppress a console window); POSIX has no such flags at all, the
    equivalent is start_new_session=True (a setsid() call before exec),
    an entirely different kwarg, not just a different flag value."""
    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        return {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {"start_new_session": True}


def new_console_creationflags() -> int:
    """creationflags for spawning a *visible* new console window (the
    "Show console windows" debug option). Windows-only concept:
    subprocess.CREATE_NEW_CONSOLE doesn't exist as an attribute at all on
    Linux, referencing it there is an AttributeError, not a no-op. On
    Linux this returns 0 (spawns normally, no separate console), the
    debug option's window becomes a no-op there rather than crashing;
    genuinely opening a new terminal emulator on Linux would need a
    terminal-emulator-specific command this project doesn't try to guess."""
    import subprocess
    return subprocess.CREATE_NEW_CONSOLE if IS_WINDOWS else 0
