"""
Standalone PySide6 entry point for the fullscreen boot/hand-off overlay,
launched as its own OS process by boot_overlay_qt.py's show(). Kept as a
separate process rather than an in-process QWidget because
start_iisu_pc.py is a synchronous script that may not be running inside
any QApplication at all, and the Manager calls show()/close() from
background threads, an in-process widget would reintroduce the "second
GUI root from a non-owning thread" problem this design exists to avoid.

Usage: python -m bridge.ui.boot_overlay_app <context> [flavor] [--status <file>]
           [--parent-pid N] [--max-seconds N]

The overlay never relies on its caller to close it. It also closes by
itself when:
  - the process that opened it (--parent-pid) is gone, so a crashed or
    killed Manager/start script can't leave it stuck on screen;
  - --max-seconds have passed;
  - the user clicks it, or presses Escape or Alt+F4. On Windows those keys
    are read globally, since Windows usually refuses to give a background
    process's window keyboard focus, so a plain keyPressEvent never fires.

With --status (a start, see bridge/start_progress.py) the bouncing bar becomes
a real progress bar with a percentage and the step being worked on, read from
that file several times a second, and the start log is shown underneath when
the file says details are on. Without it (waiting on a game to open, where
nothing can say how far along it is) the bar bounces as before.
"""

import argparse
import math
import os
import sys
import time
from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from shared.platform_compat import IS_WINDOWS
from shared.qt_theme import Fonts

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

import start_progress

# Same brand gradient used elsewhere (shared/qt_theme.py's GRADIENT_STOPS),
# kept in sync by eye since this overlay is a standalone process with
# its own tiny theme, not a consumer of that module.
_GRADIENT_HEX = ["#71e0ff", "#68ccff", "#5e84ff", "#8258fa", "#c56eff"]

_THEMES = {
    "dark": {
        "bg": QColor(4, 4, 6),
        "title": QColor("#5e84ff"),
        "context": QColor(240, 240, 245),
        "flavor": QColor(140, 140, 150),
        "track": QColor(40, 40, 44),
        "gradient_alpha": 40,
    },
    "light": {
        "bg": QColor(250, 250, 252),
        "title": QColor("#3a5ac4"),
        "context": QColor(28, 28, 34),
        "flavor": QColor(108, 108, 120),
        "track": QColor(222, 224, 232),
        "gradient_alpha": 110,
    },
}


def _detect_linux_theme() -> str:
    """Best-effort light/dark detection via the freedesktop desktop
    portal (org.freedesktop.appearance color-scheme), the modern,
    desktop-environment-agnostic standard GNOME, KDE Plasma, and most
    others implement, so this doesn't need a separate code path per
    desktop environment. Falls back to gsettings (GNOME/Cinnamon
    specifically, for older setups predating the portal) and finally to
    "dark" (matching this project's own brand theme) if neither is
    available or the query fails for any reason, same "cosmetic, never
    worth failing over" reasoning as the Windows registry read."""
    import subprocess
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
            # Response embeds a uint32: 0=no preference, 1=prefer-dark, 2=prefer-light.
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
            # "default"/"prefer-light" both mean "no explicit dark
            # preference", only an explicit dark string counts, same
            # reasoning as the portal case above: a false "light" read
            # confidently claiming a preference nobody actually set is
            # worse than just falling through to the safe default below.
            if "dark" in value:
                return "dark"
            if "light" in value:
                return "light"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "dark"


def _detect_windows_theme() -> str:
    """Reads the same registry value Windows' own Settings > Colors page
    ("Choose your default app mode") writes, best-effort, since a
    loading screen guessing wrong about system theme is purely cosmetic,
    never worth failing the boot sequence over."""
    if not IS_WINDOWS:
        return _detect_linux_theme()
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


class _AnimatedBackground(QWidget):
    """A slow-rotating, low-alpha linear gradient laid over a flat theme
    background, subtle by design (the brand gradient at ~20% opacity,
    ticking a few degrees a second) rather than a full-strength moving
    rainbow, which would fight with the text sitting on top of it."""

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._angle = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def _tick(self) -> None:
        self._angle = (self._angle + 0.3) % 360.0
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._theme["bg"])

        rad = math.radians(self._angle)
        cx, cy = self.width() / 2, self.height() / 2
        radius = math.hypot(cx, cy)
        x1, y1 = cx + radius * math.cos(rad), cy + radius * math.sin(rad)
        x2, y2 = cx - radius * math.cos(rad), cy - radius * math.sin(rad)

        gradient = QLinearGradient(x1, y1, x2, y2)
        alpha = self._theme["gradient_alpha"]
        stop_count = len(_GRADIENT_HEX)
        for i, hex_color in enumerate(_GRADIENT_HEX):
            color = QColor(hex_color)
            color.setAlpha(alpha)
            gradient.setColorAt(i / (stop_count - 1), color)
        painter.fillRect(self.rect(), gradient)


class _BounceBar(QWidget):
    """A small filled rectangle bouncing back and forth across a track,
    Qt has no built-in "indeterminate" progress style that looks like this,
    so it's hand-painted the same way the original WinForms Panel-in-a-
    Panel + Timer version was."""

    def __init__(self, theme: dict, parent=None, width: int = 320, height: int = 4, fill_width: int = 90):
        super().__init__(parent)
        self._theme = theme
        self._track_width = width
        self._fill_width = fill_width
        self.setFixedSize(width, height)
        self._x = 0
        self._direction = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(12)

    def _tick(self) -> None:
        max_x = self._track_width - self._fill_width
        new_x = self._x + 3 * self._direction
        if new_x <= 0:
            new_x, self._direction = 0, 1
        elif new_x >= max_x:
            new_x, self._direction = max_x, -1
        self._x = new_x
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._theme["track"])
        painter.fillRect(self._x, 0, self._fill_width, self.height(), self._theme["title"])


class _ProgressBar(QWidget):
    """A track filled from the left with the brand gradient, as far as the
    percentage it was last given."""

    def __init__(self, theme: dict, parent=None, width: int = 420, height: int = 8):
        super().__init__(parent)
        self._theme = theme
        self._percent = 0.0
        self.setFixedSize(width, height)

    def set_percent(self, percent: float) -> None:
        self._percent = max(0.0, min(100.0, percent))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._theme["track"])
        painter.drawRoundedRect(self.rect(), radius, radius)
        filled = int(self.width() * self._percent / 100)
        if filled <= 0:
            return
        gradient = QLinearGradient(0, 0, self.width(), 0)
        for i, hex_color in enumerate(_GRADIENT_HEX):
            gradient.setColorAt(i / (len(_GRADIENT_HEX) - 1), QColor(hex_color))
        painter.setBrush(gradient)
        painter.drawRoundedRect(0, 0, max(filled, self.height()), self.height(), radius, radius)


def _pid_alive(pid: int) -> bool:
    if IS_WINDOWS:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _dismiss_keys_down() -> bool:
    """Escape, or Alt+F4, held right now, regardless of which window has focus."""
    if not IS_WINDOWS:
        return False
    import ctypes
    user32 = ctypes.windll.user32
    VK_ESCAPE, VK_MENU, VK_F4 = 0x1B, 0x12, 0x73

    def down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    return down(VK_ESCAPE) or (down(VK_MENU) and down(VK_F4))


# How often the status file is re-read and the bar moved.
STATUS_POLL_MS = 100
LOG_LINES_SHOWN = 14


class OverlayWindow(QWidget):
    _TITLE_TEXT = "iiSU-PC Starting"

    def __init__(
        self, context: str, flavor: str, status_path: Path | None = None,
        parent_pid: int | None = None, max_seconds: float = 0,
    ):
        super().__init__()
        self._status_path = status_path
        self._shown_percent = 0.0
        self._shown_log: list[str] = []
        theme = _THEMES[_detect_windows_theme()]

        # Tool: hides it from the taskbar/Alt-Tab, you shouldn't be able
        # to switch *to* a loading overlay, only have it appear over you.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.BlankCursor)
        # Belt-and-suspenders against a default white flash before the
        # animated background's first paintEvent fires. An ID selector
        # (not a bare type/property rule) so this doesn't cascade onto
        # every child QLabel and paint each one an opaque box, Qt
        # stylesheets otherwise inherit down the widget tree like CSS.
        self.setObjectName("overlayRoot")
        self.setStyleSheet(f"#overlayRoot {{ background-color: {theme['bg'].name()}; }}")

        screen = QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())

        background = _AnimatedBackground(theme, self)
        background.setGeometry(self.rect())

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(18)
        layout.setContentsMargins(48, 48, 48, 48)

        # Bahnschrift SemiBold (bundled with Windows 10+), not this
        # project's usual UI font, a blockier, more technical/console-ish
        # face echoing iiSU's own branding without using any of iiSU's
        # actual (copyrighted, not ours to include) font files.
        self._title = QLabel(self._TITLE_TEXT)
        self._title.setFont(Fonts.brand(32))
        self._title.setStyleSheet(f"color: {theme['title'].name()};")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title)

        self._context_label = QLabel(context)
        self._context_label.setFont(Fonts.ui(14, semibold=True))
        self._context_label.setStyleSheet(f"color: {theme['context'].name()};")
        self._context_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._context_label)

        self._progress_bar = None
        self._percent_label = None
        self._log_view = None
        if status_path is None:
            layout.addWidget(_BounceBar(theme, self), alignment=Qt.AlignmentFlag.AlignCenter)
        else:
            self._progress_bar = _ProgressBar(theme, self)
            self._progress_bar.setAccessibleName("Start progress")
            layout.addWidget(self._progress_bar, alignment=Qt.AlignmentFlag.AlignCenter)
            # The number as text: a bar alone says nothing to someone who
            # can't make out how full it is, or to a screen reader.
            self._percent_label = QLabel("0%")
            self._percent_label.setFont(Fonts.ui(16, semibold=True))
            self._percent_label.setStyleSheet(f"color: {theme['context'].name()};")
            self._percent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._percent_label.setAccessibleName("Start progress in percent")
            layout.addWidget(self._percent_label)

        flavor_font = Fonts.ui(10)
        flavor_font.setItalic(True)
        flavor_label = QLabel(flavor)
        flavor_label.setFont(flavor_font)
        flavor_label.setStyleSheet(f"color: {theme['flavor'].name()};")
        flavor_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(flavor_label)

        if status_path is not None:
            # The start log, for "Show details on the loading screen". Hidden
            # until the status file asks for it, so it costs nothing otherwise.
            self._log_view = QPlainTextEdit()
            self._log_view.setReadOnly(True)
            self._log_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self._log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._log_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            log_font = QFont("Consolas", 10)
            log_font.setStyleHint(QFont.StyleHint.Monospace)
            self._log_view.setFont(log_font)
            self._log_view.setAccessibleName("Start log")
            width = min(1100, screen.geometry().width() - 160)
            self._log_view.setFixedSize(width, self._log_view.fontMetrics().lineSpacing() * LOG_LINES_SHOWN + 24)
            background = QColor(theme["track"])
            background.setAlpha(170)
            self._log_view.setStyleSheet(
                f"QPlainTextEdit {{ color: {theme['context'].name()}; border: 1px solid {theme['track'].name()}; "
                f"border-radius: 8px; padding: 8px; background-color: rgba({background.red()}, {background.green()}, "
                f"{background.blue()}, {background.alpha()}); }}"
            )
            self._log_view.hide()
            layout.addSpacing(12)
            layout.addWidget(self._log_view, alignment=Qt.AlignmentFlag.AlignCenter)

            self._status_timer = QTimer(self)
            self._status_timer.timeout.connect(self._refresh_status)
            self._status_timer.start(STATUS_POLL_MS)
            self._refresh_status()

        hint_label = QLabel("Press Esc or click to hide this screen")
        hint_label.setFont(Fonts.ui(9))
        hint_label.setStyleSheet(f"color: {theme['flavor'].name()};")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(24)
        layout.addWidget(hint_label)

        self._parent_pid = parent_pid
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._check_parent)
        if parent_pid:
            self._watch_timer.start(1000)
        if max_seconds and max_seconds > 0:
            QTimer.singleShot(int(max_seconds * 1000), self._dismiss)
        # Ignore keys already held when the overlay appears (e.g. the Escape
        # that was used to quit a game a moment ago).
        self._keys_were_down = _dismiss_keys_down()
        self._key_timer = QTimer(self)
        self._key_timer.timeout.connect(self._check_keys)
        self._key_timer.start(50)

        # Cycles 0..3 dots on a fixed-length string (padded with trailing
        # spaces) so the label's width, and therefore its centering,
        # never jitters as the dot count changes.
        self._dot_count = 0
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_timer.start(450)

    def _refresh_status(self) -> None:
        """Re-reads the status file and moves the bar. The bar never goes
        backwards, and closes a gap in a few steps, not one jump."""
        status = start_progress.read_status(self._status_path)
        if status is None:
            return
        target = start_progress.status_percent(status, time.time())
        if target > self._shown_percent:
            self._shown_percent = min(target, self._shown_percent + max(0.4, (target - self._shown_percent) * 0.3))
        self._progress_bar.set_percent(self._shown_percent)
        whole = int(self._shown_percent)
        if self._percent_label.text() != f"{whole}%":
            self._percent_label.setText(f"{whole}%")
            self._progress_bar.setAccessibleDescription(f"{whole} percent")
        label = str(status.get("label") or "")
        if label and self._context_label.text() != label:
            self._context_label.setText(label)

        show_log = bool(status.get("details"))
        if self._log_view.isVisible() != show_log:
            self._log_view.setVisible(show_log)
        log = status.get("log")
        if show_log and isinstance(log, list):
            lines = [str(line) for line in log[-LOG_LINES_SHOWN:]]
            if lines != self._shown_log:
                self._shown_log = lines
                self._log_view.setPlainText("\n".join(lines))
                bar = self._log_view.verticalScrollBar()
                bar.setValue(bar.maximum())

    def _dismiss(self) -> None:
        QApplication.quit()

    def _check_parent(self) -> None:
        if self._parent_pid and not _pid_alive(self._parent_pid):
            self._dismiss()

    def _check_keys(self) -> None:
        down = _dismiss_keys_down()
        if down and not self._keys_were_down:
            self._dismiss()
        self._keys_were_down = down

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._dismiss()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, _event) -> None:
        self._dismiss()

    def closeEvent(self, event) -> None:
        event.accept()
        self._dismiss()

    def _tick_dots(self) -> None:
        self._dot_count = (self._dot_count + 1) % 4
        dots = "." * self._dot_count
        padding = " " * (3 - self._dot_count)
        self._title.setText(f"{self._TITLE_TEXT}{dots}{padding}")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for child in self.findChildren(_AnimatedBackground):
            child.setGeometry(self.rect())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("context", nargs="?", default="Loading...")
    parser.add_argument("flavor", nargs="?", default="")
    parser.add_argument("--status", type=Path, default=None)
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--max-seconds", type=float, default=0)
    args, _unknown = parser.parse_known_args(argv)
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    app = QApplication(sys.argv[:1])
    window = OverlayWindow(args.context, args.flavor, args.status, args.parent_pid, args.max_seconds)
    if IS_WINDOWS:
        window.show()
    else:
        # Windows honors a plain frameless window's explicit setGeometry()
        # covering the whole screen (what __init__ already sets up), but
        # Wayland compositors flatly refuse to let a client position its
        # own top-level window at all, by design/security model, not a
        # missing feature, setGeometry() is silently ignored there. A real
        # fullscreen *request* (xdg_toplevel.set_fullscreen on Wayland,
        # _NET_WM_STATE_FULLSCREEN on X11) is the only portable way to get
        # a true fullscreen window on Linux regardless of display server.
        window.showFullScreen()
    window.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
