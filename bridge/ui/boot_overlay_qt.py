"""
Qt replacement for bridge/boot_overlay.py, kept as a drop-in with the exact
same three-function API (show/close/notify_error) so nothing calling it
(start_iisu_pc.py, launch_bridge.py, manager.py) needs to change at all
once this replaces the original -- only what runs *inside* the spawned
process changes, from PowerShell+WinForms to PySide6
(bridge/ui/boot_overlay_app.py and tray_notify_app.py).

Still a separate OS process, not an in-process QWidget, and for the same
reason as before: start_iisu_pc.py is a synchronous script that may not be
running inside any QApplication at all, and manager.py calls this from
background threads -- an in-process widget would reintroduce the "second
GUI root from a non-owning thread" problem a separate process avoids
entirely, Tk or Qt.
"""

import random
import subprocess
import sys
from pathlib import Path

# Explicit, not inherited from whatever the caller's own cwd happens to be:
# "-m bridge.ui.boot_overlay_app" resolves that module against the CHILD
# process's working directory, not the parent's sys.path. Without this,
# the child silently fails to find the "bridge" package the instant the
# caller's cwd isn't the project root -- confirmed live: it works when
# launched from the project root and fails invisibly (stdout/stderr are
# DEVNULL below, on purpose, for production) from anywhere else.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# A random one of these accompanies the real context line every time the
# overlay shows -- some genuine-sounding, some not, same as any loading
# screen's flavor text. Purely cosmetic: nothing here reflects anything
# actually happening.
_FLAVOR_LINES = [
    "Reticulating splines...",
    "Charging the flux capacitor...",
    "Waking up the Android...",
    "Untangling controller cables...",
    "Asking nicely for more RAM...",
    "Feeding the hamsters...",
    "Aligning the pixels...",
    "Negotiating with the GPU...",
    "Warming up the emulator...",
    "Counting to infinity (almost there)...",
    "Polishing the loading bar...",
    "Convincing Windows this is normal...",
    "Summoning the boot animation...",
    "Downloading more RAM...",
    "Dusting off old save states...",
    "Herding packets...",
    "Locating the any key...",
    "Calibrating the flux...",
    "Spinning up the virtual disc drive...",
    "Reading the manual (never)...",
]


def show(context: str) -> subprocess.Popen | None:
    """Best-effort: returns None instead of raising if the overlay process
    can't be started for any reason -- a missing overlay is a cosmetic
    regression, never a reason to fail an actual start or game launch.

    context is a short status line (e.g. "Booting Community-iiSU-PC..." or
    "Waiting on DuckStation...") describing what's actually happening;
    paired with a randomly-picked, purely-for-fun line underneath. Passed
    as real argv entries (not interpolated into a shell string the way the
    PowerShell version had to escape into), so there's nothing to escape
    here at all."""
    flavor = random.choice(_FLAVOR_LINES)
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "bridge.ui.boot_overlay_app", context, flavor],
            cwd=str(_PROJECT_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


def notify_error(title: str, message: str) -> None:
    """Best-effort Windows tray balloon notification -- the bridge
    normally runs with no visible window at all (see manager.py's
    debug_show_console_windows), so a launch failure (no emulator mapped,
    executable/rom not found, or an emulator exiting with a non-zero
    code) would otherwise be visible only in a log file nobody's looking
    at. Fire-and-forget and detached, same reasoning as show()'s overlay:
    a notification failing to display is never a reason to fail or delay
    the launch it's reporting on."""
    try:
        subprocess.Popen(
            [sys.executable, "-m", "bridge.ui.tray_notify_app", title, message],
            cwd=str(_PROJECT_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def close(overlay: subprocess.Popen | None) -> None:
    """Forcibly kills the overlay process rather than trying to close its
    window gracefully -- it has no state to lose and no user input to
    flush, so instant is strictly better here than any delay."""
    if overlay is None:
        return
    try:
        overlay.terminate()
    except OSError:
        pass
