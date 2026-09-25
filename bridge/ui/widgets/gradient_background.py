"""Slow-drifting soft gradient blobs, painted behind a page's real
content -- unlike bridge/ui/boot_overlay_app.py's single rotating linear
sweep (subtle by design there, since that screen is only up for a few
seconds), this is meant to sit visible for extended periods, so the
motion needs to actually read as motion rather than needing a screenshot
diff to notice.

Not a QSS background: painted directly so each blob can independently
drift along its own slow elliptical path, blended at low alpha over a
flat base color so page text stays readable on top of it.
"""

import math

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

from shared.qt_theme import BG, GRADIENT_STOPS

# (color, radius fraction of the widget's diagonal, drift amplitude
# fraction, angular speed, phase) -- speeds are irrational-ish relative
# to each other so the blobs never fall into a repeating lockstep pattern.
_BLOBS = [
    {"color": GRADIENT_STOPS[0], "radius": 0.42, "amp": 0.30, "speed": 0.021, "phase": 0.0},
    {"color": GRADIENT_STOPS[2], "radius": 0.48, "amp": 0.26, "speed": 0.017, "phase": 2.1},
    {"color": GRADIENT_STOPS[4], "radius": 0.38, "amp": 0.34, "speed": 0.026, "phase": 4.4},
]
_BLOB_ALPHA = 70
_TICK_MS = 50


class GradientBlobBackground(QWidget):
    def __init__(self, parent=None, base_color: str = BG):
        super().__init__(parent)
        self._base_color = QColor(base_color)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(_TICK_MS)

    def _tick(self) -> None:
        self._t += _TICK_MS / 1000.0
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._base_color)

        w, h = self.width(), self.height()
        diagonal = math.hypot(w, h)
        cx, cy = w / 2, h / 2

        for blob in _BLOBS:
            angle = self._t * blob["speed"] * 2 * math.pi + blob["phase"]
            x = cx + math.cos(angle) * blob["amp"] * w
            y = cy + math.sin(angle * 0.8) * blob["amp"] * h
            radius = blob["radius"] * diagonal

            color = QColor(blob["color"])
            color.setAlpha(_BLOB_ALPHA)
            transparent = QColor(blob["color"])
            transparent.setAlpha(0)

            gradient = QRadialGradient(x, y, radius)
            gradient.setColorAt(0.0, color)
            gradient.setColorAt(1.0, transparent)
            painter.fillRect(self.rect(), gradient)
