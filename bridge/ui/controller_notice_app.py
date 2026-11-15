"""
Standalone PySide6 entry point for the "this PlayStation controller needs
Steam Input" notice, launched as its own OS process by
controller_bridge.notify_unmapped_sony_controller(). The bridge has no
window of its own to show it from.

A small always-on-top message with OK and a "Don't show this again" box.
Ticking the box is remembered (controller_bridge.dismiss_controller_notice)
and the notice is never shown again.

Usage: python -m bridge.ui.controller_notice_app <controller name>
"""

import sys

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox

import controller_bridge
from shared.qt_theme import apply_theme

TITLE = "Controller needs Steam Input"
DONT_SHOW_AGAIN = "Don't show this again"


def build_notice(controller_name: str) -> QMessageBox:
    box = QMessageBox()
    # Over iiSU's own window, which is usually maximized in front.
    box.setWindowFlags(box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    box.setWindowTitle(TITLE)
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(controller_bridge.controller_notice_message(controller_name))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    # Owned by the box and kept on it: a check box with no parent and no
    # Python reference was freed while the box still pointed at it, which
    # crashed the notice.
    box.dont_show_again = QCheckBox(DONT_SHOW_AGAIN, box)
    box.setCheckBox(box.dont_show_again)
    return box


def finish(box: QMessageBox) -> None:
    """Remembers the choice once the notice is closed, however it was closed."""
    if box.dont_show_again.isChecked():
        try:
            controller_bridge.dismiss_controller_notice()
        except OSError:
            pass  # it is shown again next time, which is the safe way to fail


def main() -> None:
    controller_name = sys.argv[1] if len(sys.argv) > 1 else "A PlayStation controller"
    app = QApplication(sys.argv[:1])
    apply_theme(app)
    box = build_notice(controller_name)
    box.exec()
    finish(box)


if __name__ == "__main__":
    main()
