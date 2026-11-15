"""bridge/launch_problems.py: the plain-words launch failures and the last
one kept for the Home page; and which emulator exits count as failures."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
for p in (PROJECT_ROOT, PROJECT_ROOT / "bridge"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import launch_problems  # noqa: E402


class LastProblemTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "problem.json"

    def test_record_load_dismiss(self):
        self.assertIsNone(launch_problems.load(self.path))
        launch_problems.record("DuckStation", "It broke.", "FAILED: x", self.path, now=100.0)
        problem = launch_problems.load(self.path)
        self.assertEqual(problem["message"], "It broke.")
        self.assertEqual(problem["detail"], "FAILED: x")
        launch_problems.dismiss(self.path)
        self.assertIsNone(launch_problems.load(self.path))

    def test_damaged_file_is_ignored(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(launch_problems.load(self.path))

    def test_describe_time(self):
        now = time.time()
        self.assertEqual(launch_problems.describe_time(now - 5, now), "just now")
        self.assertEqual(launch_problems.describe_time(now - 120, now), "2 minutes ago")
        self.assertEqual(launch_problems.describe_time(now - 60, now), "1 minute ago")

    def test_messages_say_where_to_fix_it(self):
        self.assertIn("PC Emulators", launch_problems.emulator_missing("PCSX2"))
        self.assertIn("PCSX2", launch_problems.emulator_missing("PCSX2"))
        self.assertIn("Games > PC", launch_problems.pc_game_missing("Teardown"))
        self.assertIn("game.iso", launch_problems.rom_missing("game.iso"))


class ExitLooksLikeFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import launch_bridge
        except Exception as exc:  # pragma: no cover; launch_bridge needs Windows APIs
            raise unittest.SkipTest(f"launch_bridge can't be imported here: {exc}")
        cls.check = staticmethod(launch_bridge.exit_looks_like_failure)

    def test_clean_exit_is_fine(self):
        self.assertFalse(self.check(0, 0.0, 5.0, -1.0))

    def test_quick_error_exit_is_a_failure(self):
        self.assertTrue(self.check(1, 100.0, 105.0, 0.0))

    def test_quit_key_is_not_a_failure(self):
        self.assertFalse(self.check(1, 100.0, 300.0, 250.0))
        self.assertFalse(self.check(1, 100.0, 103.0, 101.0))

    def test_error_exit_after_playing_is_not_a_failure(self):
        self.assertFalse(self.check(1, 100.0, 100.0 + 600, 0.0))


if __name__ == "__main__":
    unittest.main()
