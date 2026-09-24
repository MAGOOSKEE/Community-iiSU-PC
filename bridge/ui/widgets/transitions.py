"""A subtle opacity cross-fade for QStackedWidget page switches -- a
capability Tk's page-swap (a plain .tkraise(), no cross-fade Tk can do at
all) never had. Understated on purpose: ~180ms, not a slide or a bounce."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


def fade_in(widget: QWidget, duration: int = 180) -> None:
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    # Clearing the effect once finished avoids leaving every page
    # permanently routed through a QGraphicsOpacityEffect layer (a real,
    # if small, paint-performance cost) long after the fade is done.
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
