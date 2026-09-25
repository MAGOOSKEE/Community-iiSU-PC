"""
Unit tests for bridge/sync_library.py's multi-disc dedup (m3u/cue) and the
Windows Apps stub-folder merge, both pure filesystem logic, no AVD or
adb needed, same as this project's other tests.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import sync_library
from console_names import load_console_lookup
from sync_library import merge_windows_stubs, referenced_disc_filenames, scan_library




class ReferencedDiscFilenamesTests(unittest.TestCase):
    def test_m3u_references_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "Game.m3u").write_text("disc1.bin\ndisc2.bin\n", encoding="utf-8")
            (directory / "disc1.bin").write_bytes(b"x")
            (directory / "disc2.bin").write_bytes(b"x")
            files = list(directory.iterdir())
            self.assertEqual(referenced_disc_filenames(files), {"disc1.bin", "disc2.bin"})

    def test_cue_file_directive_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "Game.cue").write_text('FILE "Game.bin" BINARY\n', encoding="utf-8")
            (directory / "Game.bin").write_bytes(b"x")
            files = list(directory.iterdir())
            self.assertEqual(referenced_disc_filenames(files), {"Game.bin"})

    def test_unrelated_files_are_not_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "Solo Game.iso").write_bytes(b"x")
            files = list(directory.iterdir())
            self.assertEqual(referenced_disc_filenames(files), set())

    def test_blank_and_comment_lines_in_m3u_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "Game.m3u").write_text("# a comment\n\ndisc1.chd\n", encoding="utf-8")
            (directory / "disc1.chd").write_bytes(b"x")
            files = list(directory.iterdir())
            self.assertEqual(referenced_disc_filenames(files), {"disc1.chd"})


class ScanLibraryDedupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exact, cls.by_compact = load_console_lookup()

    def test_multi_disc_m3u_game_becomes_one_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            roms_dir = Path(tmp)
            psx = roms_dir / "Playstation 1"
            psx.mkdir()
            (psx / "Final Fantasy VII.m3u").write_text(
                "Final Fantasy VII (Disc 1).bin\nFinal Fantasy VII (Disc 2).bin\n", encoding="utf-8"
            )
            (psx / "Final Fantasy VII (Disc 1).bin").write_bytes(b"x")
            (psx / "Final Fantasy VII (Disc 2).bin").write_bytes(b"x")

            consoles, skipped = scan_library(roms_dir, self.exact, self.by_compact)
            self.assertEqual(skipped, [])
            names = [rel for rel, _size, _mtime in consoles["psx"]]
            self.assertEqual(names, ["Final Fantasy VII.m3u"])

    def test_single_disc_cue_bin_pair_becomes_one_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            roms_dir = Path(tmp)
            psx = roms_dir / "Playstation 1"
            psx.mkdir()
            (psx / "Solo Game.cue").write_text('FILE "Solo Game.bin" BINARY\n', encoding="utf-8")
            (psx / "Solo Game.bin").write_bytes(b"x")

            consoles, _skipped = scan_library(roms_dir, self.exact, self.by_compact)
            names = [rel for rel, _size, _mtime in consoles["psx"]]
            self.assertEqual(names, ["Solo Game.cue"])

    def test_dedup_is_scoped_per_directory(self):
        # Two different game folders, each with their own disc.bin, the
        # first folder's .m3u must not swallow the second folder's file of
        # the same name.
        with tempfile.TemporaryDirectory() as tmp:
            roms_dir = Path(tmp)
            psx = roms_dir / "Playstation 1"
            game_a = psx / "Game A"
            game_b = psx / "Game B"
            game_a.mkdir(parents=True)
            game_b.mkdir(parents=True)
            (game_a / "Game A.m3u").write_text("disc.bin\n", encoding="utf-8")
            (game_a / "disc.bin").write_bytes(b"x")
            (game_b / "disc.bin").write_bytes(b"x")

            consoles, _skipped = scan_library(roms_dir, self.exact, self.by_compact)
            names = sorted(rel for rel, _size, _mtime in consoles["psx"])
            self.assertEqual(names, ["Game A/Game A.m3u", "Game B/disc.bin"])

    def test_dedupe_exception_shows_discs_and_hides_the_playlist(self):
        # The Gran Turismo 2 case: an .m3u whose "discs" are actually
        # distinct modes, not continuation discs, exempting it should
        # show its individual files as their own entries and hide the
        # collapsed .m3u entry, the opposite of the usual direction,
        # without affecting any other multi-disc game.
        with tempfile.TemporaryDirectory() as tmp:
            roms_dir = Path(tmp)
            psx = roms_dir / "Playstation 1"
            gt2 = psx / "Gran Turismo 2"
            gt2.mkdir(parents=True)
            (gt2 / "Gran Turismo 2.m3u").write_text("Arcade.bin\nSimulation.bin\n", encoding="utf-8")
            (gt2 / "Arcade.bin").write_bytes(b"x")
            (gt2 / "Simulation.bin").write_bytes(b"x")

            exception = "psx/Gran Turismo 2/Gran Turismo 2.m3u"
            consoles, _skipped = scan_library(roms_dir, self.exact, self.by_compact, dedupe_exceptions={exception})
            names = sorted(rel for rel, _size, _mtime in consoles["psx"])
            self.assertEqual(names, ["Gran Turismo 2/Arcade.bin", "Gran Turismo 2/Simulation.bin"])

    def test_exception_is_per_playlist_not_per_folder(self):
        # Two m3u files sharing a folder, exempting one (hiding it,
        # showing its disc instead) must not affect the other.
        with tempfile.TemporaryDirectory() as tmp:
            roms_dir = Path(tmp)
            psx = roms_dir / "Playstation 1"
            psx.mkdir(parents=True)
            (psx / "GT2.m3u").write_text("gt2disc.bin\n", encoding="utf-8")
            (psx / "gt2disc.bin").write_bytes(b"x")
            (psx / "Normal.m3u").write_text("normaldisc.bin\n", encoding="utf-8")
            (psx / "normaldisc.bin").write_bytes(b"x")

            consoles, _skipped = scan_library(
                roms_dir, self.exact, self.by_compact, dedupe_exceptions={"psx/GT2.m3u"}
            )
            names = sorted(rel for rel, _size, _mtime in consoles["psx"])
            self.assertEqual(names, ["Normal.m3u", "gt2disc.bin"])


class DedupeExceptionsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patch = patch.object(sync_library, "DEDUPE_EXCEPTIONS_PATH", Path(self._tmp.name) / "dedupe_exceptions.json")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_missing_file_returns_empty_set(self):
        self.assertEqual(sync_library.load_dedupe_exceptions(), set())

    def test_round_trip(self):
        sync_library.save_dedupe_exceptions({"psx/Gran Turismo 2/Gran Turismo 2.m3u"})
        self.assertEqual(sync_library.load_dedupe_exceptions(), {"psx/Gran Turismo 2/Gran Turismo 2.m3u"})


class MergeWindowsStubsTests(unittest.TestCase):
    def test_merge_adds_windows_console(self):
        consoles = {"psx": [("Game.cue", 4096, 0)]}
        with patch.object(sync_library, "scan_windows_stubs", return_value=[("Notepad.pcgame", 4096, 0)]):
            merge_windows_stubs(consoles)
        self.assertEqual(consoles["windows"], [("Notepad.pcgame", 4096, 0)])

    def test_merge_dedups_against_legacy_folder_entries(self):
        consoles = {"windows": [("Notepad.pcgame", 100, 1)]}
        with patch.object(sync_library, "scan_windows_stubs", return_value=[("Notepad.pcgame", 4096, 2)]):
            merge_windows_stubs(consoles)
        self.assertEqual(consoles["windows"], [("Notepad.pcgame", 4096, 2)])

    def test_merge_is_a_noop_when_no_stubs_exist(self):
        consoles = {"psx": [("Game.cue", 4096, 0)]}
        with patch.object(sync_library, "scan_windows_stubs", return_value=[]):
            merge_windows_stubs(consoles)
        self.assertNotIn("windows", consoles)


if __name__ == "__main__":
    unittest.main()
