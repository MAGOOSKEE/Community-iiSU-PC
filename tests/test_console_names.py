"""
Unit tests for bridge/console_names.py's folder-name -> iiSU-shortname
resolution, used by both sync_library.py and manager.py's ROM Directory
status check.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from console_names import load_console_lookup, resolve_console_shortname


class ResolveConsoleShortnameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exact, cls.by_compact = load_console_lookup()

    def resolve(self, name):
        return resolve_console_shortname(name, self.exact, self.by_compact)

    def test_manual_override_wins(self):
        self.assertEqual(self.resolve("Playstation 1"), "psx")
        self.assertEqual(self.resolve("Playstation 2"), "ps2")

    def test_case_insensitive(self):
        self.assertEqual(self.resolve("PLAYSTATION 1"), "psx")

    def test_compact_match_ignores_punctuation_and_spacing(self):
        # e.g. "Nintendo-64" / "Nintendo 64" / "nintendo64" should all
        # resolve the same way once punctuation/spacing is stripped.
        result_a = self.resolve("Nintendo 64")
        result_b = self.resolve("Nintendo-64")
        self.assertEqual(result_a, result_b)

    def test_unknown_folder_returns_none(self):
        self.assertIsNone(self.resolve("Definitely Not A Real Console"))

    def test_short_code_does_not_false_positive_substring_match(self):
        # The substring fallback only applies to keys >= 5 chars, precisely
        # to avoid a short code like "gc" matching inside an unrelated name.
        self.assertIsNone(self.resolve("gcxyz_not_a_console"))


if __name__ == "__main__":
    unittest.main()
