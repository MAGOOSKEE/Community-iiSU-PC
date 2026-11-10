"""A config.json that exists but can't be read: detected with a reason, never
mistaken for "not set up", and recoverable from a backup or the defaults."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import bridge_config  # noqa: E402


class ConfigFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.config = self.root / "config.json"
        for name, value in (
            ("CONFIG_PATH", self.config),
            ("AVD_HOME", self.root / "avd-home"),
        ):
            patcher = patch.object(bridge_config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _damaged_files(self):
        return list(self.root.glob("config.damaged-*.json"))

    # == Detection ==

    def test_a_missing_config_is_not_a_damaged_one(self):
        with self.assertRaises(bridge_config.ConfigMissingError) as raised:
            bridge_config.load_config()
        self.assertNotIsInstance(raised.exception, bridge_config.ConfigInvalidError)
        self.assertIsNone(bridge_config.config_problem())

    def test_a_cut_short_file_says_where_it_breaks(self):
        self.config.write_text('{\n  "bridge_port": 7737,\n  "roms_dir": "C:/Ro', encoding="utf-8")
        with self.assertRaises(bridge_config.ConfigInvalidError) as raised:
            bridge_config.load_config()
        self.assertIn("line 3", raised.exception.reason)
        self.assertIn("Manager", str(raised.exception))

    def test_an_empty_file_is_damaged(self):
        self.config.write_text("", encoding="utf-8")
        self.assertEqual(bridge_config.config_problem(), "the file is empty")

    def test_valid_json_that_is_not_settings_is_damaged(self):
        self.config.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertIsNotNone(bridge_config.config_problem())

    def test_a_file_that_is_not_text_is_damaged(self):
        self.config.write_bytes(b"\xff\xfe\x00\x00\x9d\x8f")
        self.assertIsNotNone(bridge_config.config_problem())

    def test_a_damaged_config_stops_scripts_the_way_a_missing_one_does(self):
        self.config.write_text("{nope", encoding="utf-8")
        with self.assertRaises(bridge_config.ConfigMissingError):
            bridge_config.load_config()

    def test_notepads_byte_order_mark_is_fine(self):
        self.config.write_text('{"bridge_port": 7737}', encoding="utf-8-sig")
        self.assertEqual(bridge_config.load_config()["bridge_port"], 7737)

    # == Saving ==

    def test_the_backup_follows_wherever_the_config_is(self):
        self.assertEqual(bridge_config.backup_path(), self.root / "config.json.bak")

    def test_saving_keeps_the_previous_settings_as_a_backup(self):
        bridge_config.save_config({"bridge_port": 1})
        bridge_config.save_config({"bridge_port": 2})
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["bridge_port"], 2)
        self.assertEqual(json.loads(bridge_config.backup_path().read_text(encoding="utf-8"))["bridge_port"], 1)
        self.assertFalse((self.root / "config.json.tmp").exists())

    def test_a_damaged_file_never_replaces_a_good_backup(self):
        bridge_config.save_config({"bridge_port": 1})
        bridge_config.save_config({"bridge_port": 2})
        self.config.write_text("{nope", encoding="utf-8")
        bridge_config.save_config({"bridge_port": 3})
        self.assertEqual(json.loads(bridge_config.backup_path().read_text(encoding="utf-8"))["bridge_port"], 1)

    def test_a_save_that_fails_leaves_the_old_file_whole(self):
        bridge_config.save_config({"bridge_port": 1})
        with patch.object(bridge_config.json, "dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                bridge_config.save_config({"bridge_port": 2})
        self.assertEqual(bridge_config.load_config()["bridge_port"], 1)

    # == Recovery ==

    def test_restore_puts_the_backup_back_and_keeps_the_damaged_file(self):
        bridge_config.save_config({"bridge_port": 1, "roms_dir": "D:/Roms"})
        bridge_config.save_config({"bridge_port": 1, "roms_dir": "E:/Roms"})
        self.config.write_text("{nope", encoding="utf-8")
        self.assertTrue(bridge_config.has_usable_backup())
        kept = bridge_config.restore_backup()
        self.assertEqual(bridge_config.load_config()["roms_dir"], "D:/Roms")
        self.assertEqual(kept.read_text(encoding="utf-8"), "{nope")
        self.assertEqual(self._damaged_files(), [kept])

    def test_no_backup_means_none_is_offered(self):
        self.config.write_text("{nope", encoding="utf-8")
        self.assertFalse(bridge_config.has_usable_backup())

    def test_a_damaged_backup_is_not_offered_or_restored(self):
        self.config.write_text("{nope", encoding="utf-8")
        bridge_config.backup_path().write_text("also nope", encoding="utf-8")
        self.assertFalse(bridge_config.has_usable_backup())
        with self.assertRaises(bridge_config.ConfigInvalidError):
            bridge_config.restore_backup()
        self.assertEqual(self.config.read_text(encoding="utf-8"), "{nope")

    def test_reset_writes_defaults_that_load_and_keeps_the_damaged_file(self):
        self.config.write_text("{nope", encoding="utf-8")
        kept = bridge_config.reset_to_defaults()
        config = bridge_config.load_config()
        self.assertEqual(config["bridge_port"], 7737)
        self.assertIn("com.retroarch", config["emulators"])
        self.assertEqual(kept.read_text(encoding="utf-8"), "{nope")

    def test_reset_keeps_pointing_at_the_vm_this_install_has(self):
        (bridge_config.AVD_HOME / "myvm.avd").mkdir(parents=True)
        self.config.write_text("{nope", encoding="utf-8")
        bridge_config.reset_to_defaults()
        config = bridge_config.load_config()
        self.assertEqual((config["avd_name"], config["iisu_window_title"], config["vm_backend"]), ("myvm", "myvm", "avd"))


if __name__ == "__main__":
    unittest.main()
