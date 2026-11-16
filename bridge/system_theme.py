"""Whether this PC is set to light or dark mode, best-effort: a wrong guess
is only cosmetic, so every failure reads as "dark" (the project's own theme).

Used by the loading screen to match its colours, and by the launch bridge to
put Android's own dark mode in step with Windows each time iiSU starts."""

import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"


def detect_linux_theme() -> str:
    """The freedesktop portal's color-scheme (GNOME, KDE and most others),
    then GNOME's gsettings for older setups."""
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
                "--object-path", "/org/freedesktop/portal/desktop",
                "--method", "org.freedesktop.portal.Settings.Read",
                "org.freedesktop.appearance", "color-scheme",
            ],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            # A uint32: 0 no preference, 1 prefer dark, 2 prefer light.
            if "uint32 1" in result.stdout:
                return "dark"
            if "uint32 2" in result.stdout:
                return "light"
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            value = result.stdout.strip().lower()
            if "dark" in value:
                return "dark"
            if "light" in value:
                return "light"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "dark"


def detect_theme() -> str:
    """"light" or "dark", from the value Windows' Settings > Colors writes
    ("Choose your default app mode")."""
    if not IS_WINDOWS:
        return detect_linux_theme()
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if value else "dark"
    except OSError:
        return "dark"


def android_night_command(theme: str) -> str:
    """The adb shell command that sets Android's dark mode to match."""
    return f"cmd uimode night {'no' if theme == 'light' else 'yes'}"


def sync_android_theme(shell, theme: str | None = None) -> tuple[str, bool]:
    """Sets Android's dark mode to this PC's. shell is adb_client.shell.
    Returns the theme applied and whether adb accepted it."""
    theme = theme or detect_theme()
    status, _output = shell(android_night_command(theme), timeout=10)
    return theme, status == 0
