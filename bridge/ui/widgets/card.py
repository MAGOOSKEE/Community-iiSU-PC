"""A dark panel with real rounded corners (shared/theme.py's tkinter Card
could only fake this with a flat rectangle -- see qt_theme.py's QSS for
the actual `border-radius`, set via the "Card" object name below) and a
soft drop shadow for a little visual lift off the page background --
another thing a flat Tk canvas tile had no way to do."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)
