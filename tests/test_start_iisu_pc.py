"""
Unit tests for bridge/start_iisu_pc.py's boot-fingerprint diffing and the
quickboot-autosave regression: set_quickboot_autosave(enabled=not
effective_cold_boot) meant a cold boot -- which includes every first run,
and any run after a settings/ROM-library change -- never saved a snapshot
to resume *from*, so the very next start's "quick resume" was never
backed by anything real. No AVD or adb needed for either.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import start_iisu_pc  # noqa: E402 -- path set up above


class DescribeBootFingerprintDiffTests(unittest.TestCase):
    def test_no_saved_state_yet(self):
        parts = {"avd_name": "iisuwin", "display": "x"}
        self.assertEqual(start_iisu_pc.describe_boot_fingerprint_diff(None, parts), ["no saved resume state yet"])

    def test_nothing_changed_is_empty(self):
        parts = {"avd_name": "iisuwin", "display": "x", "roms_dir": "y"}
        self.assertEqual(start_iisu_pc.describe_boot_fingerprint_diff(dict(parts), parts), [])

    def test_reports_only_the_parts_that_changed(self):
        old = {"avd_name": "iisuwin", "display": "x", "roms_dir": "y"}
        new = {"avd_name": "iisuwin", "display": "z", "roms_dir": "y"}
        self.assertEqual(start_iisu_pc.describe_boot_fingerprint_diff(old, new), ["the display settings changed"])

    def test_combined_fingerprint_is_order_independent_and_sensitive_to_changes(self):
        cfg_a = {"avd_name": "iisuwin", "display": {"dpi": 240}, "emulators": {}, "usb_passthrough": [], "roms_dir": "C:/nonexistent"}
        cfg_b = {"avd_name": "iisuwin", "display": {"dpi": 320}, "emulators": {}, "usb_passthrough": [], "roms_dir": "C:/nonexistent"}
        self.assertEqual(
            start_iisu_pc.compute_boot_fingerprint(cfg_a), start_iisu_pc.compute_boot_fingerprint(cfg_a)
        )
        self.assertNotEqual(
            start_iisu_pc.compute_boot_fingerprint(cfg_a), start_iisu_pc.compute_boot_fingerprint(cfg_b)
        )


class QuickbootAutosaveRegressionTests(unittest.TestCase):
    """start_avd() must always leave quickboot autosave ON before launching,
    regardless of whether this particular boot is cold or a resume -- the
    boot-fingerprint check is what decides whether a saved snapshot gets
    trusted later; autosave being off would mean there's never one to
    trust in the first place."""

    def _run_start_avd(self, force_cold_boot: bool):
        with patch.object(start_iisu_pc, "find_system_emulator_exe", return_value=Path("C:/fake/emulator.exe")), \
             patch.object(start_iisu_pc, "ensure_portable_sdk", return_value={"ANDROID_SDK_ROOT": "C:/fake/sdk"}), \
             patch.object(start_iisu_pc, "clear_stale_locks"), \
             patch.object(start_iisu_pc, "patch_config_ini"), \
             patch.object(start_iisu_pc, "set_quickboot_autosave") as mock_autosave, \
             patch.object(start_iisu_pc, "_launch_once", return_value=12345):
            pid = start_iisu_pc.start_avd("iisuwin", [], force_cold_boot=force_cold_boot)
            self.assertEqual(pid, 12345)
            return mock_autosave

    def test_autosave_enabled_after_a_cold_boot(self):
        mock_autosave = self._run_start_avd(force_cold_boot=True)
        mock_autosave.assert_called_once()
        self.assertIs(mock_autosave.call_args.kwargs["enabled"], True)

    def test_autosave_enabled_after_a_resume(self):
        mock_autosave = self._run_start_avd(force_cold_boot=False)
        mock_autosave.assert_called_once()
        self.assertIs(mock_autosave.call_args.kwargs["enabled"], True)


if __name__ == "__main__":
    unittest.main()
