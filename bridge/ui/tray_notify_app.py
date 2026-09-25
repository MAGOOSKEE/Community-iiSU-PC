"""
Standalone PySide6 entry point for a one-shot Windows tray balloon
notification, launched as its own OS process by boot_overlay_qt.py's
notify_error(), replacing the PowerShell+WinForms NotifyIcon balloon in
bridge/boot_overlay.py. The bridge normally runs with no visible window at
all, so a launch failure would otherwise be visible only in a log file
nobody's looking at.

Usage: python -m bridge.ui.tray_notify_app <title> <message>
"""

import sys

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon


def main() -> None:
    title = sys.argv[1] if len(sys.argv) > 1 else "Community-iiSU-PC"
    message = sys.argv[2] if len(sys.argv) > 2 else ""

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    icon = QSystemTrayIcon(app.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
    icon.show()
    icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Warning, 8000)
    icon.messageClicked.connect(app.quit)
    QTimer.singleShot(9000, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
