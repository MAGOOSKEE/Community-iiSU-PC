"""A horizontal stacked bar showing what fills a disk, one colour per
category, with a tooltip for the segment under the pointer. The numbers and
colours come from shared/vm_storage_breakdown.py; this only draws them."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from shared import vm_storage_breakdown as bd
from shared.qt_theme import PANEL_BG_HOVER

BAR_HEIGHT = 30
RADIUS = 8
MIN_VISIBLE_PX = 3  # a tiny but non-zero category should still be seen


class StorageBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._segments: list[bd.Segment] = []
        self._spans: list[tuple[float, float, bd.Segment]] = []

    def set_segments(self, segments: list[bd.Segment]) -> None:
        self._segments = list(segments)
        self.update()

    def _layout(self, width: float) -> list[tuple[float, float, bd.Segment]]:
        """(x, width, segment) for each segment that has any size. Tiny ones get
        a minimum width, taken proportionally from the rest."""
        shown = [(segment, fraction) for segment, fraction in bd.bar_fractions(self._segments) if segment.kb > 0]
        if not shown or width <= 0:
            return []
        widths = [max(fraction * width, MIN_VISIBLE_PX) for _s, fraction in shown]
        scale = width / sum(widths)
        spans, x = [], 0.0
        for (segment, _f), w in zip(shown, widths):
            spans.append((x, w * scale, segment))
            x += w * scale
        return spans

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        clip = QPainterPath()
        clip.addRoundedRect(rect, RADIUS, RADIUS)
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor(PANEL_BG_HOVER))

        self._spans = self._layout(rect.width())
        for x, w, segment in self._spans:
            painter.fillRect(QRectF(x, 0, w, rect.height()), QColor(segment.color))
        # Thin separators so neighbouring colours never blur together.
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
        for x, _w, _segment in self._spans[1:]:
            painter.drawLine(int(x), 0, int(x), self.height())

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        for start, w, segment in self._spans:
            if start <= x <= start + w:
                QToolTip.showText(event.globalPosition().toPoint(), f"{segment.label}: {bd.format_kb(segment.kb)}", self)
                return
        QToolTip.hideText()
