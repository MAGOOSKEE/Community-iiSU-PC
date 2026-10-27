"""
Unit tests for bridge/hotkey_keys.py: the stored key names the quit and shutdown
hotkeys use and their Windows virtual-key codes, plus a check that the key
capture dialog in the onboarding wizard produces exactly the names the bridge
understands.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import hotkey_keys  # noqa: E402; path set up above

try:
    import PySide6  # noqa: F401
except ImportError:  # pragma: no cover; depends on the environment
    PySide6 = None


class ResolveVkTests(unittest.TestCase):
    def test_named_keys(self):
        self.assertEqual(hotkey_keys.resolve_vk("escape"), 0x1B)
        self.assertEqual(hotkey_keys.resolve_vk("space"), 0x20)
        self.assertEqual(hotkey_keys.resolve_vk("prior"), 0x21)

    def test_names_are_case_insensitive(self):
        self.assertEqual(hotkey_keys.resolve_vk("Escape"), hotkey_keys.resolve_vk("escape"))

    def test_function_keys(self):
        self.assertEqual(hotkey_keys.resolve_vk("f1"), 0x70)
        self.assertEqual(hotkey_keys.resolve_vk("f12"), 0x7B)

    def test_plain_letters_and_digits_are_their_character_code(self):
        self.assertEqual(hotkey_keys.resolve_vk("q"), ord("Q"))
        self.assertEqual(hotkey_keys.resolve_vk("5"), ord("5"))

    def test_an_empty_key_is_zero(self):
        self.assertEqual(hotkey_keys.resolve_vk(""), 0)


class ModifierTableTests(unittest.TestCase):
    def test_flags_are_distinct_bits(self):
        values = list(hotkey_keys.MODIFIER_FLAGS.values())
        self.assertEqual(len(set(values)), len(values))
        for value in values:
            self.assertEqual(value & (value - 1), 0, "each flag is a single bit")

    def test_both_tables_cover_the_same_modifiers(self):
        self.assertEqual(set(hotkey_keys.MODIFIER_FLAGS), set(hotkey_keys.MODIFIER_VK))


@unittest.skipIf(PySide6 is None, "PySide6 is not installed")
class WizardKeyNamesMatchTheBridgeTests(unittest.TestCase):
    def test_the_capture_dialog_writes_exactly_the_names_the_bridge_reads(self):
        from bridge.ui import onboarding_wizard

        self.assertEqual(set(onboarding_wizard._NAMED_KEYS.values()), set(hotkey_keys.NAMED_KEY_VK))


if __name__ == "__main__":
    unittest.main()
