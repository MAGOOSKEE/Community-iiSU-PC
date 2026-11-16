"""The cyan-to-purple line drawn under every page header, as one reusable
widget. It can also be a progress bar (set_fraction): the gradient then fills
from the left over a dim track, so Setup and onboarding show progress in the
line already under their title instead of a second bar beside it."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from shared.qt_theme import PANEL_BG_HOVER, gradient_brush


class GradientDivider(QWidget):
    def __init__(self, parent=None, height: int = 3):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._fraction: float | None = None

    def set_fraction(self, fraction: float | None) -> None:
        """None draws the plain divider; 0 to 1 draws that much progress."""
        self._fraction = None if fraction is None else max(0.0, min(1.0, fraction))
        self.update()

    def fraction(self) -> float | None:
        return self._fraction

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2
        if self._fraction is None:
            painter.fillRect(self.rect(), gradient_brush(self.width()))
            return
        painter.setBrush(QColor(PANEL_BG_HOVER))
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        filled = self.width() * self._fraction
        if filled > 0:
            painter.setBrush(gradient_brush(self.width()))
            painter.drawRoundedRect(QRectF(0, 0, filled, self.height()), radius, radius)
