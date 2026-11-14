"""The iiPC mark as a widget, at any height, sharp on a scaled display.

Still by default (the top of the sidebar). With animated=True the "ii" is
filled with the brand gradient, drifting slowly through it, while "PC" stays
white (the Home page). The picture is bridge/assets/iipc_mark.png, white on
transparent, drawn from docs/images/brand/iipc-mark.svg by
scripts/render_brand_assets.py.

The gradient is laid over the picture rather than the mark being redrawn
from shapes here, so a new logo only needs that one file replaced. The one
thing this file knows about the artwork is where the "ii" ends (II_FRACTION).
"""

import math
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from shared.qt_theme import GRADIENT_STOPS

MARK_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "iipc_mark.png"
# The "ii" takes up the left quarter of the mark; this falls in the gap before the "P".
II_FRACTION = 0.29
_TICK_MS = 40
_CYCLE_SECONDS = 7.0


class BrandMark(QWidget):
    def __init__(self, height: int, animated: bool = False, parent=None):
        super().__init__(parent)
        self._animated = animated
        self._t = 0.0
        self._mark: QImage | None = None
        source = QImage(str(MARK_PATH))
        if source.isNull():
            # No picture, no widget: the layout closes up around it.
            self.setFixedSize(0, 0)
            return
        ratio = self.devicePixelRatioF()
        self._mark = source.scaledToHeight(
            round(height * ratio), Qt.TransformationMode.SmoothTransformation
        ).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._mark.setDevicePixelRatio(ratio)
        self.setFixedSize(QSize(round(self._mark.width() / ratio), height))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def is_available(self) -> bool:
        return self._mark is not None

    # Only ticks while it can be seen.
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._animated and self._mark is not None:
            self._timer.start(_TICK_MS)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._mark is not None:
            self._timer.stop()

    def _tick(self) -> None:
        self._t = (self._t + _TICK_MS / 1000.0) % _CYCLE_SECONDS
        self.update()

    def _gradient_ii(self) -> QImage:
        """The "ii" strip of the mark, filled with the brand gradient at its
        current position. The mark's own shading (the grey seam between the
        two strokes) is kept by multiplying it into the gradient."""
        mark = self._mark
        width, height = math.ceil(mark.width() * II_FRACTION), mark.height()
        strip = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        strip.fill(Qt.GlobalColor.transparent)

        # Runs diagonally and reflects, so it loops without a visible jump.
        span = height * 1.4
        shift = (self._t / _CYCLE_SECONDS) * 2 * span
        gradient = QLinearGradient(-shift, -shift, span - shift, span - shift)
        gradient.setSpread(QLinearGradient.Spread.ReflectSpread)
        last = len(GRADIENT_STOPS) - 1
        for i, stop in enumerate(GRADIENT_STOPS):
            gradient.setColorAt(i / last, QColor(stop))

        source = mark.copy(0, 0, width, height)
        source.setDevicePixelRatio(1.0)
        painter = QPainter(strip)
        painter.fillRect(strip.rect(), gradient)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.drawImage(0, 0, source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, source)
        painter.end()
        return strip

    def paintEvent(self, _event) -> None:
        if self._mark is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self._animated:
            painter.drawImage(0, 0, self._mark)
            return
        frame = self._mark.copy()
        strip = self._gradient_ii()
        composer = QPainter(frame)
        composer.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        composer.drawImage(0, 0, strip)
        composer.end()
        frame.setDevicePixelRatio(self._mark.devicePixelRatio())
        painter.drawImage(0, 0, frame)
