"""One line of text that never wraps: anything wider than the label is cut
short with an ellipsis and shown in full in the tooltip.

Its height is always one line, so a long message can't change the layout
around it. A word-wrapped QLabel in a centred column reports the height for
the wrong width, and on the Home page that let a wrapped status line draw
over the Start button."""

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", width: int = 520, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self._explicit_tooltip = ""
        self._width = width
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802; Qt's own name
        self._full_text = text
        self._refresh()

    def text(self) -> str:
        return self._full_text

    def setToolTip(self, tooltip: str) -> None:  # noqa: N802; Qt's own name
        """A tooltip set here wins over the automatic full-text one."""
        self._explicit_tooltip = tooltip
        self._refresh()

    def sizeHint(self) -> QSize:  # noqa: N802; Qt's own name
        return QSize(self._width, self.fontMetrics().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802; Qt's own name
        return self.sizeHint()

    def resizeEvent(self, event) -> None:  # noqa: N802; Qt's own name
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event) -> None:  # noqa: N802; Qt's own name
        # A stylesheet or font change alters the text width and line height. Only
        # those: _refresh sets the tooltip, which raises a change event of its own.
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.updateGeometry()
            self._refresh()

    def _refresh(self) -> None:
        shown = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, self._width)
        super().setText(shown)
        cut = shown != self._full_text
        super().setToolTip(self._explicit_tooltip or (self._full_text if cut else ""))
