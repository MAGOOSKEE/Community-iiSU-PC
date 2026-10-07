"""Unit tests for bridge/services/steam_shortcuts_service.py: the binary VDF
format, adding/updating/removing the shortcut, file discovery, and
Steam Deck / gamescope detection."""

import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import steam_shortcuts_service as svc  # noqa: E402; path set up above


def _sample_bytes() -> bytes:
    """A shortcuts.vdf laid out byte for byte the way Steam writes one."""
    return (
        b"\x00shortcuts\x00"
        b"\x000\x00"
        b"\x02appid\x00" + struct.pack("<I", 0x80ABCDEF) +
        b"\x01AppName\x00Some Game\x00"
        b"\x01Exe\x00\"C:\\g\\game.exe\"\x00"
        b"\x02IsHidden\x00" + struct.pack("<I", 0) +
        b"\x00tags\x00\x01" b"0\x00fav\x00\x08"  # tags: {"0": "fav"}
        b"\x08"  # end of entry 0
        b"\x08"  # end of shortcuts
        b"\x08"  # end of file
    )


class VdfFormatTests(unittest.TestCase):
    def test_parses_the_steam_layout(self):
        tree = svc.parse_vdf(_sample_bytes())
        entry = tree["shortcuts"]["0"]
        self.assertEqual(entry["appid"], 0x80ABCDEF)
        self.assertEqual(entry["AppName"], "Some Game")
        self.assertEqual(entry["Exe"], '"C:\\g\\game.exe"')
        self.assertEqual(entry["IsHidden"], 0)
        self.assertEqual(entry["tags"], {"0": "fav"})

    def test_round_trip_is_byte_exact(self):
        self.assertEqual(svc.serialize_vdf(svc.parse_vdf(_sample_bytes())), _sample_bytes())

    def test_empty_shortcuts_round_trip(self):
        data = b"\x00shortcuts\x00\x08\x08"
        self.assertEqual(svc.serialize_vdf(svc.parse_vdf(data)), data)

    def test_unicode_survives(self):
        tree = {"shortcuts": {"0": {"AppName": "ゲーム \u2603"}}}
        self.assertEqual(svc.parse_vdf(svc.serialize_vdf(tree)), tree)

    def test_truncated_file_is_an_error_not_a_crash(self):
        with self.assertRaises(svc.SteamShortcutError):
            svc.parse_vdf(_sample_bytes()[:-5])

    def test_unknown_field_type_refuses_instead_of_guessing(self):
        data = b"\x00shortcuts\x00\x000\x00\x07weird\x00" + b"\x00" * 8 + b"\x08\x08\x08"
        with self.assertRaisesRegex(svc.SteamShortcutError, "doesn't handle"):
            svc.parse_vdf(data)

    def test_ints_are_written_as_unsigned_32_bit(self):
        out = svc.serialize_vdf({"x": 0xFFFFFFFF})
        self.assertEqual(out, b"\x02x\x00\xff\xff\xff\xff\x08")


class EntryTests(unittest.TestCase):
    def test_app_id_has_the_high_bit_and_is_stable(self):
        a = svc.shortcut_app_id('"C:\\x.exe"', "Name")
        self.assertGreaterEqual(a, 0x80000000)
        self.assertEqual(a, svc.shortcut_app_id('"C:\\x.exe"', "Name"))
        self.assertNotEqual(a, svc.shortcut_app_id('"C:\\x.exe"', "Other"))

    def test_build_entry_quotes_exe_and_start_dir(self):
        entry = svc.build_entry("N", "C:\\py\\python.exe", "C:\\proj", "\"script.py\"")
        self.assertEqual(entry["Exe"], '"C:\\py\\python.exe"')
        self.assertEqual(entry["StartDir"], '"C:\\proj"')
        self.assertEqual(entry["appid"], svc.shortcut_app_id(entry["Exe"], "N"))

    def test_entry_has_the_fields_steam_writes_in_its_order(self):
        # Field order taken from a shortcuts.vdf written by a current Steam client.
        expected = [
            "appid", "AppName", "Exe", "StartDir", "icon", "ShortcutPath", "LaunchOptions", "IsHidden",
            "AllowDesktopConfig", "AllowOverlay", "OpenVR", "Devkit", "DevkitGameID", "DevkitOverrideAppID",
            "LastPlayTime", "FlatpakAppID", "sortas", "tags",
        ]
        self.assertEqual(list(svc.build_entry("N", "x", "y")), expected)

    def test_build_entry_does_not_double_quote(self):
        entry = svc.build_entry("N", '"C:\\py.exe"', '"C:\\d"')
        self.assertEqual(entry["Exe"], '"C:\\py.exe"')
        self.assertEqual(entry["StartDir"], '"C:\\d"')

    def test_upsert_adds_with_the_next_numeric_key(self):
        tree = {"shortcuts": {"0": {"AppName": "A"}, "1": {"AppName": "B"}}}
        self.assertEqual(svc.upsert_entry(tree, svc.build_entry("C", "x", "y")), "added")
        self.assertEqual(sorted(tree["shortcuts"]), ["0", "1", "2"])

    def test_upsert_into_an_empty_file(self):
        tree: dict = {}
        self.assertEqual(svc.upsert_entry(tree, svc.build_entry("C", "x", "y")), "added")
        self.assertEqual(list(tree["shortcuts"]), ["0"])

    def test_upsert_updates_in_place_and_keeps_play_time_and_tags(self):
        old = svc.build_entry("C", "old", "y")
        old["LastPlayTime"] = 1234
        old["tags"] = {"0": "favorites"}
        tree = {"shortcuts": {"0": {"AppName": "Other"}, "1": old}}
        self.assertEqual(svc.upsert_entry(tree, svc.build_entry("C", "new", "y")), "updated")
        entry = tree["shortcuts"]["1"]
        self.assertEqual(entry["Exe"], '"new"')
        self.assertEqual(entry["LastPlayTime"], 1234)
        self.assertEqual(entry["tags"], {"0": "favorites"})
        self.assertEqual(len(tree["shortcuts"]), 2)

    def test_remove_renumbers_the_rest(self):
        tree = {"shortcuts": {"0": {"AppName": "A"}, "1": {"AppName": "Gone"}, "2": {"AppName": "C"}}}
        self.assertTrue(svc.remove_entry(tree, "Gone"))
        self.assertEqual({k: v["AppName"] for k, v in tree["shortcuts"].items()}, {"0": "A", "1": "C"})
        self.assertFalse(svc.remove_entry(tree, "Gone"))

    def test_built_entry_survives_serialization(self):
        tree = {"shortcuts": {}}
        svc.upsert_entry(tree, svc.build_entry("N", "exe", "dir", "opts"))
        self.assertEqual(svc.parse_vdf(svc.serialize_vdf(tree)), tree)


class FileTests(unittest.TestCase):
    def test_write_backs_up_the_previous_file_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config" / "shortcuts.vdf"
            self.assertIsNone(svc.write_shortcuts_file(path, {"shortcuts": {}}))
            backup = svc.write_shortcuts_file(path, {"shortcuts": {"0": {"AppName": "A"}}})
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_bytes(), svc.serialize_vdf({"shortcuts": {}}))
            self.assertEqual(svc.parse_vdf(path.read_bytes())["shortcuts"]["0"]["AppName"], "A")
            self.assertEqual([p.name for p in path.parent.glob("*tmp")], [])

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(svc.read_shortcuts_file(Path("Z:/nope/shortcuts.vdf")), {"shortcuts": {}})

    def test_discovery_finds_real_accounts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("12345", "67890", "0", "ac_cache"):
                (root / "userdata" / name).mkdir(parents=True)
            found = [p.parent.parent.name for p in svc.shortcuts_files([root])]
            self.assertEqual(found, ["12345", "67890"])

    def test_discovery_with_no_userdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(svc.shortcuts_files([Path(tmp)]), [])


class AddRemoveTests(unittest.TestCase):
    def test_refuses_while_steam_is_running(self):
        with patch.object(svc, "steam_is_running", return_value=True), self.assertRaisesRegex(svc.SteamShortcutError, "Close Steam"):
            svc.add_to_steam(files=[Path("x")])
        with patch.object(svc, "steam_is_running", return_value=True), self.assertRaises(svc.SteamShortcutError):
            svc.remove_from_steam(files=[Path("x")])

    def test_no_account_found_is_explained(self):
        with patch.object(svc, "steam_is_running", return_value=False), patch.object(svc, "shortcuts_files", return_value=[]):
            with self.assertRaisesRegex(svc.SteamShortcutError, "user account"):
                svc.add_to_steam()

    def test_add_twice_updates_and_remove_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(svc, "steam_is_running", return_value=False):
            path = Path(tmp) / "config" / "shortcuts.vdf"
            self.assertEqual(svc.add_to_steam(files=[path])[0][1], "added")
            self.assertEqual(svc.add_to_steam(files=[path])[0][1], "updated")
            self.assertEqual(len(svc.read_shortcuts_file(path)["shortcuts"]), 1)
            self.assertEqual(svc.remove_from_steam(files=[path]), 1)
            self.assertEqual(svc.read_shortcuts_file(path)["shortcuts"], {})
            self.assertEqual(svc.remove_from_steam(files=[path]), 0)

    def test_other_shortcuts_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(svc, "steam_is_running", return_value=False):
            path = Path(tmp) / "shortcuts.vdf"
            path.write_bytes(_sample_bytes())
            svc.add_to_steam(files=[path])
            tree = svc.read_shortcuts_file(path)
            self.assertEqual(tree["shortcuts"]["0"]["AppName"], "Some Game")
            self.assertEqual(tree["shortcuts"]["1"]["AppName"], svc.SHORTCUT_NAME)

    def test_a_file_this_cannot_parse_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(svc, "steam_is_running", return_value=False):
            path = Path(tmp) / "shortcuts.vdf"
            bad = b"\x00shortcuts\x00\x000\x00\x07weird\x00" + b"\x00" * 8 + b"\x08\x08\x08"
            path.write_bytes(bad)
            with self.assertRaises(svc.SteamShortcutError):
                svc.add_to_steam(files=[path])
            self.assertEqual(path.read_bytes(), bad)

    def test_launcher_entry_runs_the_start_script(self):
        entry = svc.launcher_entry(python_exe="C:\\py\\python.exe", start_script=Path("C:/proj/bridge/start_iisu_pc.py"))
        self.assertEqual(entry["AppName"], svc.SHORTCUT_NAME)
        self.assertIn("start_iisu_pc.py", entry["LaunchOptions"])
        self.assertTrue(entry["LaunchOptions"].startswith('"'))


class DeckDetectionTests(unittest.TestCase):
    def test_gamescope_session(self):
        self.assertTrue(svc.is_gamescope_session({"XDG_CURRENT_DESKTOP": "gamescope"}))
        self.assertTrue(svc.is_gamescope_session({"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}))
        self.assertFalse(svc.is_gamescope_session({"XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertFalse(svc.is_gamescope_session({}))

    def test_steam_deck_from_dmi(self):
        self.assertTrue(svc.is_steam_deck("Valve", "Jupiter", {}))
        self.assertTrue(svc.is_steam_deck("Valve", "Galileo", {}))
        self.assertFalse(svc.is_steam_deck("ASUSTeK", "ROG", {}))
        self.assertFalse(svc.is_steam_deck("Valve", "Index", {}))

    def test_steam_deck_env_fallback(self):
        self.assertTrue(svc.is_steam_deck("x", "y", {"SteamDeck": "1"}))
        self.assertFalse(svc.is_steam_deck("x", "y", {"SteamDeck": "0"}))


if __name__ == "__main__":
    unittest.main()
