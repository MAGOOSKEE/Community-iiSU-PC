"""Scaled aspect-ratio preview box showing the configured display
resolution as a to-scale rectangle, with the resolution and simplified
ratio labeled underneath, consolidates what used to be two near-
identical, already-drifted-apart copies (bridge/manager.py's Display page
and bridge/onboarding_wizard.py's Display step: the wizard's copy was
missing the ratio label the Manager's had). This version always shows
both, built once here instead of twice."""

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from shared.qt_theme import GRADIENT_STOPS, INPUT_BG, PANEL_BG_HOVER, TEXT, TEXT_DIM, Fonts

_LABEL_HEIGHT = 18
_MARGIN = 10


class DisplayPreview(QWidget):
    def __init__(self, parent=None, box_width: int = 160, box_height: int = 110):
        super().__init__(parent)
        self._width = 1920
        self._height = 1080
        self.setFixedSize(box_width, box_height)

    def set_resolution(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(INPUT_BG))

        if self._width <= 0 or self._height <= 0:
            return

        box_w, box_h = self.width(), self.height()
        draw_h = box_h - _LABEL_HEIGHT
        scale = min((box_w - _MARGIN * 2) / self._width, (draw_h - _MARGIN * 2) / self._height)
        rect_w, rect_h = self._width * scale, self._height * scale
        x0, y0 = (box_w - rect_w) / 2, (draw_h - rect_h) / 2

        painter.setPen(QPen(QColor(GRADIENT_STOPS[2]), 2))
        painter.setBrush(QColor(PANEL_BG_HOVER))
        painter.drawRect(QRectF(x0, y0, rect_w, rect_h))

        painter.setPen(QColor(TEXT))
        painter.setFont(Fonts.body())
        painter.drawText(QRectF(0, 0, box_w, draw_h), Qt.AlignmentFlag.AlignCenter, f"{self._width}×{self._height}")

        divisor = math.gcd(self._width, self._height) or 1
        painter.setPen(QColor(TEXT_DIM))
        painter.drawText(
            QRectF(0, draw_h, box_w, _LABEL_HEIGHT), Qt.AlignmentFlag.AlignCenter,
            f"{self._width // divisor}:{self._height // divisor}",
        )
