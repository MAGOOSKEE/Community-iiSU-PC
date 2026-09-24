"""Small colored status indicator (up/down/unknown) -- replaces
manager.py's tkinter StatusDot(tk.Canvas)."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from shared.qt_theme import GRAY, GREEN, RED

_COLORS = {"up": GREEN, "down": RED, "unknown": GRAY}


class StatusDot(QWidget):
    def __init__(self, parent=None, size: int = 10):
        super().__init__(parent)
        self._size = size
        self._state = "unknown"
        self.setFixedSize(QSize(size, size))

    def set_state(self, state: str) -> None:
        if state not in _COLORS:
            raise ValueError(f"unknown StatusDot state: {state!r}")
        self._state = state
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_COLORS[self._state]))
        painter.drawEllipse(0, 0, self._size, self._size)
