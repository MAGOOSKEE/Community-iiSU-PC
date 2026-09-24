"""Manager entry point: `pythonw.exe -m bridge.ui.app` (see the packaging
plan) replaces the old `python manager.py`. Mirrors manager.py's own
module-level bootstrap (Pillow/tkinterdnd2 auto-install), except
tkinterdnd2 is gone entirely (Qt has native drag-and-drop) and PySide6
itself now needs the same treatment tkinterdnd2 used to get -- it's a
dependency of this file's own imports below, so ensure_pyside6() has to
run before any of them, not inside main()."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "installer"))
from setup_wizard import ensure_pillow, ensure_pyside6

ensure_pyside6()

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import QApplication

from bridge.ui.main_window import ManagerWindow
from shared.qt_theme import apply_theme


def main() -> None:
    ensure_pillow()
    app = QApplication(sys.argv)
    apply_theme(app)
    window = ManagerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
