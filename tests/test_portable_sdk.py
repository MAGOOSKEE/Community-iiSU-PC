"""
Unit tests for the pure file handling in bridge/portable_sdk.py: patching
config.ini, the quickboot autosave switch and deleting the resume snapshot.
Only temporary folders are touched, no SDK or AVD is needed.
"""

import sys
import tempfile
import unittest
import unittest.mock
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

    def test_a_snapshot_is_complete_only_with_every_file_a_save_writes(self):
        snapshot = self.avd / "snapshots" / "default_boot"
        self.assertFalse(portable_sdk.has_complete_quickboot_snapshot(self.avd))
        snapshot.mkdir(parents=True)
        # What the spare install was left with after a save that never happened.
        (snapshot / "snapshot.pb").write_bytes(b"x")
        (snapshot / "ram.img").write_bytes(b"x")
        self.assertFalse(portable_sdk.has_complete_quickboot_snapshot(self.avd))
        for name in portable_sdk.QUICKBOOT_SNAPSHOT_FILES:
            (snapshot / name).write_bytes(b"x")
        self.assertTrue(portable_sdk.has_complete_quickboot_snapshot(self.avd))


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


class ResetAvdDataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.avd = Path(self._tmp.name) / "iisuwin.avd"
        self.avd.mkdir()
        (self.avd / "userdata-qemu.img.qcow2").write_bytes(b"data")
        (self.avd / "cache.img").write_bytes(b"cache")
        (self.avd / "snapshots" / "default_boot").mkdir(parents=True)
        (self.avd / "config.ini").write_bytes(b"keep")
        (self.avd / "system.img").write_bytes(b"keep")

    def test_deleting_removes_the_data_files_and_keeps_the_rest(self):
        handled = portable_sdk.reset_avd_data(self.avd)
        self.assertEqual(sorted(handled), ["cache.img", "snapshots", "userdata-qemu.img.qcow2"])
        self.assertEqual(sorted(p.name for p in self.avd.iterdir()), ["config.ini", "system.img"])

    def test_moving_aside_keeps_the_data_recoverable(self):
        aside = Path(self._tmp.name) / "aside"
        portable_sdk.reset_avd_data(self.avd, aside=aside)
        self.assertEqual((aside / "userdata-qemu.img.qcow2").read_bytes(), b"data")
        self.assertFalse((self.avd / "userdata-qemu.img.qcow2").exists())

    def test_an_already_empty_avd_handles_nothing(self):
        portable_sdk.reset_avd_data(self.avd)
        self.assertEqual(portable_sdk.reset_avd_data(self.avd), [])


class ImageSysdirAndBootstrapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_reads_the_image_sysdir_from_config_ini(self):
        (self.root / "config.ini").write_text("hw.ramSize=2048\nimage.sysdir.1=system-images/android-34/x86_64/\n", encoding="utf-8")
        self.assertEqual(portable_sdk._read_image_sysdir(self.root), "system-images/android-34/x86_64/")

    def test_no_config_or_no_entry_is_none(self):
        self.assertIsNone(portable_sdk._read_image_sysdir(self.root))
        (self.root / "config.ini").write_text("hw.ramSize=2048\n", encoding="utf-8")
        self.assertIsNone(portable_sdk._read_image_sysdir(self.root))

    def test_bootstrapped_needs_the_emulator_adb_and_the_avd_config(self):
        sdk = self.root / "sdk"
        avd_home = self.root / "avd-home"
        with unittest.mock.patch.object(portable_sdk, "PORTABLE_SDK", sdk), \
             unittest.mock.patch.object(portable_sdk, "PORTABLE_AVD_HOME", avd_home):
            self.assertFalse(portable_sdk.is_bootstrapped("iisuwin"))
            for path in (
                sdk / "emulator" / portable_sdk.EMULATOR_BIN,
                sdk / "platform-tools" / portable_sdk.ADB_BIN,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            self.assertFalse(portable_sdk.is_bootstrapped("iisuwin"))
            config = avd_home / "iisuwin.avd" / "config.ini"
            config.parent.mkdir(parents=True)
            config.write_bytes(b"x")
            self.assertTrue(portable_sdk.is_bootstrapped("iisuwin"))
            self.assertFalse(portable_sdk.is_bootstrapped("other"))


if __name__ == "__main__":
    unittest.main()
