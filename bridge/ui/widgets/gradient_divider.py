"""The cyan-to-purple decorative divider drawn under every page header,
replaces shared/theme.py's draw_gradient_bar(), which had to fake a
gradient with a strip of 1px-wide canvas lines under tkinter. One reusable
widget instead of 5+ copy-pasted canvas-drawing call sites."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from shared.qt_theme import gradient_brush


class GradientDivider(QWidget):
    def __init__(self, parent=None, height: int = 3):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), gradient_brush(self.width()))
