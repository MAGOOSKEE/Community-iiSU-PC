"""The "Map a key" dialog: every key can be mapped, Escape included, and
cancelling is a button (or closing the window), not a key."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QDialog
    HAVE_QT = True
except ImportError:  # the app needs PySide6; the test suite can run without it
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class KeyCaptureDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        import bridge.ui  # noqa: F401; puts bridge/ and installer/ on sys.path
        from bridge.ui.onboarding_wizard import KeyCaptureDialog
        cls.KeyCaptureDialog = KeyCaptureDialog

    def _press(self, key, text="", modifiers=None):
        dialog = self.KeyCaptureDialog()
        event = QKeyEvent(QKeyEvent.Type.KeyPress, key, modifiers or Qt.KeyboardModifier.NoModifier, text)
        dialog.keyPressEvent(event)
        return dialog

    def test_escape_is_captured_not_treated_as_cancel(self):
        dialog = self._press(Qt.Key.Key_Escape)
        self.assertEqual(dialog.captured_key, "escape")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_space_and_enter_are_captured_too(self):
        self.assertEqual(self._press(Qt.Key.Key_Space, " ").captured_key, "space")
        self.assertEqual(self._press(Qt.Key.Key_Return, "\r").captured_key, "return")

    def test_a_letter_and_a_function_key_are_captured(self):
        self.assertEqual(self._press(Qt.Key.Key_Q, "q").captured_key, "q")
        self.assertEqual(self._press(Qt.Key.Key_F10).captured_key, "f10")

    def test_a_modifier_alone_keeps_listening(self):
        dialog = self._press(Qt.Key.Key_Control)
        self.assertIsNone(dialog.captured_key)
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_the_cancel_button_cancels_without_a_key(self):
        dialog = self.KeyCaptureDialog()
        dialog.cancel_button.click()
        self.assertIsNone(dialog.captured_key)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_the_cancel_button_never_takes_the_keyboard(self):
        # A focused button would swallow Space and Enter, which are mappable.
        dialog = self.KeyCaptureDialog()
        self.assertEqual(dialog.cancel_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertFalse(dialog.cancel_button.autoDefault())
        self.assertFalse(dialog.cancel_button.isDefault())


if __name__ == "__main__":
    unittest.main()
