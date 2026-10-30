"""Unit tests for shared/platform_compat.py's gui_python(): the interpreter a
desktop or Steam shortcut starts. Temporary folders only."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared import platform_compat  # noqa: E402; path set up above


class GuiPythonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.console = self.root / "python.exe"
        self.console.write_bytes(b"")

    def test_a_console_python_on_windows_becomes_pythonw(self):
        (self.root / "pythonw.exe").write_bytes(b"")
        with patch.object(platform_compat, "IS_WINDOWS", True):
            self.assertEqual(platform_compat.gui_python(str(self.console)), str(self.root / "pythonw.exe"))

    def test_the_name_is_matched_ignoring_case(self):
        (self.root / "pythonw.exe").write_bytes(b"")
        with patch.object(platform_compat, "IS_WINDOWS", True):
            self.assertEqual(Path(platform_compat.gui_python(str(self.root / "Python.EXE"))).name.lower(), "pythonw.exe")

    def test_without_a_pythonw_beside_it_the_console_python_is_kept(self):
        with patch.object(platform_compat, "IS_WINDOWS", True):
            self.assertEqual(platform_compat.gui_python(str(self.console)), str(self.console))

    def test_pythonw_itself_is_kept(self):
        windowed = self.root / "pythonw.exe"
        windowed.write_bytes(b"")
        with patch.object(platform_compat, "IS_WINDOWS", True):
            self.assertEqual(platform_compat.gui_python(str(windowed)), str(windowed))

    def test_linux_keeps_the_interpreter_it_was_given(self):
        (self.root / "pythonw.exe").write_bytes(b"")
        with patch.object(platform_compat, "IS_WINDOWS", False):
            self.assertEqual(platform_compat.gui_python("/usr/bin/python3"), str(Path("/usr/bin/python3")))

    def test_defaults_to_the_running_interpreter(self):
        with patch.object(platform_compat, "IS_WINDOWS", False):
            self.assertEqual(platform_compat.gui_python(), str(Path(sys.executable)))


if __name__ == "__main__":
    unittest.main()
