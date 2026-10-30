"""
The bundled runtime and Setup install PySide6-Essentials, not the full PySide6
(which adds PySide6-Addons: QtWebEngine and friends, over 400 MB). That only
works while the code imports nothing from the add-ons, which would fail on a
fresh install and nowhere else, so this checks every import against the
modules Essentials ships.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

import build_embedded_python  # noqa: E402; path set up above

# The Qt modules in the PySide6-Essentials wheel.
ESSENTIALS_MODULES = {
    "QtConcurrent", "QtCore", "QtDBus", "QtDesigner", "QtGui", "QtHelp", "QtNetwork", "QtOpenGL",
    "QtOpenGLWidgets", "QtPrintSupport", "QtQml", "QtQuick", "QtQuickControls2", "QtQuickTest",
    "QtQuickWidgets", "QtSql", "QtSvg", "QtSvgWidgets", "QtTest", "QtUiTools", "QtWidgets", "QtXml",
}
SOURCE_DIRS = ("bridge", "installer", "shared", "tests")


class RuntimePackageTests(unittest.TestCase):
    def test_the_runtime_bundles_essentials_not_the_full_pyside6(self):
        self.assertIn("PySide6-Essentials", build_embedded_python.RUNTIME_PACKAGES)
        self.assertNotIn("PySide6", build_embedded_python.RUNTIME_PACKAGES)

    def test_no_code_imports_a_qt_module_outside_essentials(self):
        used = {}
        pattern = re.compile(r"PySide6\.(Qt\w+)")
        for folder in SOURCE_DIRS:
            for path in (PROJECT_ROOT / folder).rglob("*.py"):
                if "android-sdk" in path.parts or "__pycache__" in path.parts:
                    continue
                for module in pattern.findall(path.read_text(encoding="utf-8", errors="replace")):
                    used.setdefault(module, path.relative_to(PROJECT_ROOT))
        outside = {module: str(path) for module, path in used.items() if module not in ESSENTIALS_MODULES}
        self.assertEqual(outside, {}, "these need PySide6-Addons, which the runtime no longer ships")
        self.assertIn("QtWidgets", used)


if __name__ == "__main__":
    unittest.main()
