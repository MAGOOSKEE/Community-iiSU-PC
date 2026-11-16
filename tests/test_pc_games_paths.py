"""bridge/pc_games_paths.py: the one-time move of an install's PC games
files from their old windows_* names."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bridge"))

import pc_games_paths  # noqa: E402


class MigrateOldNamesTests(unittest.TestCase):
    def test_moves_both_old_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp)
            (bridge / "windows_apps.json").write_text("{}", encoding="utf-8")
            (bridge / "windows_stubs").mkdir()
            (bridge / "windows_stubs" / "Game.pcgame").write_text("", encoding="utf-8")
            moved = pc_games_paths.migrate_old_names(bridge)
            self.assertEqual(len(moved), 2)
            self.assertTrue((bridge / "pc_games.json").is_file())
            self.assertTrue((bridge / "pc_game_stubs" / "Game.pcgame").is_file())
            self.assertFalse((bridge / "windows_apps.json").exists())

    def test_new_names_win_and_old_ones_are_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp)
            (bridge / "windows_apps.json").write_text('{"old": 1}', encoding="utf-8")
            (bridge / "pc_games.json").write_text('{"new": 1}', encoding="utf-8")
            self.assertEqual(pc_games_paths.migrate_old_names(bridge), [])
            self.assertEqual((bridge / "pc_games.json").read_text(encoding="utf-8"), '{"new": 1}')
            self.assertTrue((bridge / "windows_apps.json").exists())

    def test_nothing_to_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pc_games_paths.migrate_old_names(Path(tmp)), [])


if __name__ == "__main__":
    unittest.main()
