"""
Unit tests for the pure file handling in bridge/portable_sdk.py: patching
config.ini, the quickboot autosave switch and deleting the resume snapshot.
Only temporary folders are touched, no SDK or AVD is needed.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import portable_sdk  # noqa: E402; path set up above


class ClearQuickbootSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.avd = Path(self._tmp.name) / "iisuwin.avd"
        self.avd.mkdir()

    def test_removes_the_quickboot_snapshot_and_nothing_else(self):
        snapshots = self.avd / "snapshots"
        (snapshots / "default_boot").mkdir(parents=True)
        (snapshots / "default_boot" / "ram.bin").write_bytes(b"x")
        (snapshots / "mine").mkdir()
        (self.avd / "userdata-qemu.img.qcow2").write_bytes(b"data")
        self.assertTrue(portable_sdk.clear_quickboot_snapshot(self.avd))
        self.assertFalse((snapshots / "default_boot").exists())
        self.assertTrue((snapshots / "mine").is_dir())
        self.assertTrue((self.avd / "userdata-qemu.img.qcow2").is_file())

    def test_reports_nothing_removed_when_there_is_no_snapshot(self):
        self.assertFalse(portable_sdk.clear_quickboot_snapshot(self.avd))
        self.assertFalse(portable_sdk.clear_quickboot_snapshot(self.avd / "missing.avd"))


class QuickbootAutosaveTests(unittest.TestCase):
    def test_writes_the_switch_both_ways(self):
        with tempfile.TemporaryDirectory() as tmp:
            avd = Path(tmp)
            portable_sdk.set_quickboot_autosave(avd, enabled=True)
            self.assertEqual((avd / "quickbootChoice.ini").read_text(encoding="utf-8"), "saveOnExit = true\n")
            portable_sdk.set_quickboot_autosave(avd, enabled=False)
            self.assertEqual((avd / "quickbootChoice.ini").read_text(encoding="utf-8"), "saveOnExit = false\n")


class PatchConfigIniTests(unittest.TestCase):
    def _patch(self, text: str, **kwargs) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            config_ini = Path(tmp) / "config.ini"
            config_ini.write_text(text, encoding="utf-8")
            portable_sdk.patch_config_ini(config_ini, **kwargs)
            lines = config_ini.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def test_cold_boot_flag_follows_the_argument(self):
        self.assertEqual(self._patch("hw.lcd.width=1080\n", force_cold_boot=True)["fastboot.forceColdBoot"], "yes")
        self.assertEqual(self._patch("fastboot.forceColdBoot=yes\n", force_cold_boot=False)["fastboot.forceColdBoot"], "no")

    def test_unrelated_keys_are_kept_and_extra_overrides_applied(self):
        result = self._patch("hw.lcd.width=1080\nhw.audioOutput=yes\n", extra_overrides={"hw.audioOutput": "no"})
        self.assertEqual(result["hw.lcd.width"], "1080")
        self.assertEqual(result["hw.audioOutput"], "no")

    def test_a_missing_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            portable_sdk.patch_config_ini(Path(tmp) / "config.ini")
            self.assertFalse((Path(tmp) / "config.ini").exists())


if __name__ == "__main__":
    unittest.main()
