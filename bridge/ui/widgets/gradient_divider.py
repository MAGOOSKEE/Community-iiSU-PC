"""The cyan-to-purple decorative divider drawn under every page header, as
one reusable widget."""

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
