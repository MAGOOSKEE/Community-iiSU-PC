"""
Standalone PySide6 entry point for the fullscreen boot/hand-off overlay --
launched as its own OS process by boot_overlay_qt.py's show(), the same
way the PowerShell+WinForms version it replaces (bridge/boot_overlay.py)
already was. Kept as a separate process rather than an in-process QWidget
for the same reason as before: start_iisu_pc.py is a synchronous script
that may not be running inside any QApplication at all, and manager.py
calls show()/close() from background threads -- an in-process widget
would reintroduce the "second GUI root from a non-owning thread" problem
this design exists to avoid.

Usage: python -m bridge.ui.boot_overlay_app <context> [flavor]
"""

import sys

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

ACCENT = QColor(94, 132, 255)
FLAVOR_COLOR = QColor(120, 120, 128)
TRACK_COLOR = QColor(40, 40, 44)


class _BounceBar(QWidget):
    """A small filled rectangle bouncing back and forth across a track --
    Qt has no built-in "indeterminate" progress style that looks like this,
    so it's hand-painted the same way the original WinForms Panel-in-a-
    Panel + Timer version was."""

    def __init__(self, parent=None, width: int = 320, height: int = 4, fill_width: int = 90):
        super().__init__(parent)
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
        painter.fillRect(self.rect(), TRACK_COLOR)
        painter.fillRect(self._x, 0, self._fill_width, self.height(), ACCENT)


class OverlayWindow(QWidget):
    def __init__(self, context: str, flavor: str):
        super().__init__()
        # Tool: hides it from the taskbar/Alt-Tab -- you shouldn't be able
        # to switch *to* a loading overlay, only have it appear over you.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setStyleSheet("background-color: black;")
        self.setCursor(Qt.CursorShape.BlankCursor)

        screen = QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        # Bahnschrift SemiBold (bundled with Windows 10+), not this
        # project's usual Segoe UI -- a blockier, more technical/console-ish
        # face echoing iiSU's own branding without using any of iiSU's
        # actual (copyrighted, not ours to include) font files.
        title = QLabel("Community-iiSU-PC")
        title.setFont(QFont("Bahnschrift SemiBold", 32))
        title.setStyleSheet("color: #5e84ff;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        context_label = QLabel(context)
        context_label.setFont(QFont("Segoe UI Semibold", 14))
        context_label.setStyleSheet("color: white;")
        context_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(context_label)

        layout.addWidget(_BounceBar(self), alignment=Qt.AlignmentFlag.AlignCenter)

        flavor_font = QFont("Segoe UI", 10)
        flavor_font.setItalic(True)
        flavor_label = QLabel(flavor)
        flavor_label.setFont(flavor_font)
        flavor_label.setStyleSheet("color: #787880;")
        flavor_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(flavor_label)


def main() -> None:
    context = sys.argv[1] if len(sys.argv) > 1 else "Loading..."
    flavor = sys.argv[2] if len(sys.argv) > 2 else ""
    app = QApplication(sys.argv)
    window = OverlayWindow(context, flavor)
    window.show()
    window.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
