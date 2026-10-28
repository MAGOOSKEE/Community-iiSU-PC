"""
Unit tests for bridge/start_iisu_pc.py's boot-fingerprint diffing and the
quickboot-autosave regression: set_quickboot_autosave(enabled=not
effective_cold_boot) meant a cold boot, which includes every first run,
and any run after a settings/ROM-library change, never saved a snapshot
to resume *from*, so the very next start's "quick resume" was never
backed by anything real. No AVD or adb needed for either.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import start_iisu_pc  # noqa: E402; path set up above


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
    regardless of whether this particular boot is cold or a resume, the
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


class HeadlessStartSequenceTests(unittest.TestCase):
    def _run(self, headless: bool, port_open: bool = False):
        with patch.object(start_iisu_pc, "is_avd_running", return_value=True), \
             patch.object(start_iisu_pc, "sync_rom_library"), \
             patch.object(start_iisu_pc, "is_port_open", return_value=port_open), \
             patch.object(start_iisu_pc, "save_state"), \
             patch.object(start_iisu_pc, "launch_iisu") as launch_iisu, \
             patch.object(start_iisu_pc, "show_iisu_window") as show_window, \
             patch.object(start_iisu_pc.subprocess, "Popen") as popen, \
             patch.object(start_iisu_pc.time, "sleep"), \
             patch("builtins.open", unittest.mock.mock_open()):
            start_iisu_pc._run_start_sequence({"iisu_fullscreen": True}, "iisuwin", 7737, False, {}, headless)
        return popen, launch_iisu, show_window

    def test_headless_does_not_start_the_bridge(self):
        popen, launch_iisu, show_window = self._run(headless=True)
        popen.assert_not_called()
        launch_iisu.assert_not_called()
        show_window.assert_not_called()

    def test_headless_leaves_an_already_running_bridge_alone(self):
        popen, launch_iisu, show_window = self._run(headless=True, port_open=True)
        popen.assert_not_called()
        launch_iisu.assert_not_called()
        show_window.assert_not_called()

    def test_a_normal_start_still_starts_the_bridge(self):
        popen, _launch_iisu, _show_window = self._run(headless=False)
        popen.assert_called_once()


class HeadlessSnapshotTests(unittest.TestCase):
    """A headless run must not resume or leave behind a quick-resume snapshot: a windowed resume
    afterwards would bring back the state from before the headless session."""

    def _start(self, headless: bool):
        with patch.object(start_iisu_pc, "is_avd_running", return_value=False), \
             patch.object(start_iisu_pc, "compute_boot_fingerprint_parts", return_value={"a": "1"}), \
             patch.object(start_iisu_pc, "load_saved_boot_fingerprint_parts", return_value={"a": "1"}), \
             patch.object(start_iisu_pc, "compute_boot_fingerprint", return_value="fp"), \
             patch.object(start_iisu_pc, "start_avd", return_value=4242) as start_avd, \
             patch.object(start_iisu_pc, "save_boot_fingerprint") as save, \
             patch.object(start_iisu_pc, "clear_boot_fingerprint") as clear, \
             patch.object(start_iisu_pc, "wait_for_android_boot", return_value=True), \
             patch.object(start_iisu_pc, "sync_rom_library"), \
             patch.object(start_iisu_pc, "save_state"), \
             patch.object(start_iisu_pc, "is_port_open", return_value=True), \
             patch.object(start_iisu_pc, "launch_iisu"), \
             patch.object(start_iisu_pc, "show_iisu_window"):
            start_iisu_pc._run_start_sequence({}, "iisuwin", 7737, False, {}, headless)
        return start_avd, save, clear

    def test_headless_always_cold_boots_and_clears_the_resume_state(self):
        start_avd, save, clear = self._start(headless=True)
        self.assertIs(start_avd.call_args.kwargs["force_cold_boot"], True)
        save.assert_not_called()
        clear.assert_called_once()

    def test_a_normal_start_with_nothing_changed_still_resumes(self):
        start_avd, save, clear = self._start(headless=False)
        self.assertIs(start_avd.call_args.kwargs["force_cold_boot"], False)
        save.assert_called_once()
        clear.assert_not_called()


class HeadlessLaunchTests(unittest.TestCase):
    def _emulator_args(self, headless: bool) -> list[str]:
        """The command line _launch_once builds, without starting anything."""
        profile = start_iisu_pc.emulator_profiles.normalize_profile({})
        with patch.object(start_iisu_pc.subprocess, "Popen") as popen, \
             patch.object(start_iisu_pc, "is_avd_running", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open()):
            start_iisu_pc._launch_once(Path("emulator.exe"), "iisuwin", {}, [], False, profile, False, headless)
            return popen.call_args.args[0]

    def test_headless_adds_no_window(self):
        self.assertIn("-no-window", self._emulator_args(headless=True))

    def test_a_normal_start_keeps_the_window(self):
        self.assertNotIn("-no-window", self._emulator_args(headless=False))

    def test_start_avd_passes_headless_through_every_attempt(self):
        with patch.object(start_iisu_pc, "find_system_emulator_exe", return_value=Path("C:/fake/emulator.exe")), \
             patch.object(start_iisu_pc, "ensure_portable_sdk", return_value={"ANDROID_SDK_ROOT": "C:/fake/sdk"}), \
             patch.object(start_iisu_pc, "clear_stale_locks"), \
             patch.object(start_iisu_pc, "patch_config_ini"), \
             patch.object(start_iisu_pc, "set_quickboot_autosave"), \
             patch.object(start_iisu_pc, "_launch_once", return_value=1) as launch:
            start_iisu_pc.start_avd("iisuwin", [], headless=True)
        self.assertIs(launch.call_args.args[-1], True)


class AndroidBootWaitTests(unittest.TestCase):
    def _wait(self, readings):
        """readings: one (listed, boot_completed) pair per poll."""
        poll = {"i": -1}

        def is_running(_name):
            poll["i"] += 1
            return readings[poll["i"]][0]

        def getprop(name):
            return readings[poll["i"]][1] if name == "sys.boot_completed" else ""

        clock = iter(range(0, 10_000, 2))
        with patch.object(start_iisu_pc, "is_avd_running", side_effect=is_running), \
             patch.object(start_iisu_pc, "_getprop", side_effect=getprop), \
             patch.object(start_iisu_pc.time, "time", side_effect=lambda: next(clock)), \
             patch.object(start_iisu_pc.time, "sleep"):
            # One clock read sets the deadline, then one per poll.
            return start_iisu_pc.wait_for_android_boot(timeout=len(readings) * 2 + 1)

    def test_needs_two_good_readings_in_a_row(self):
        self.assertTrue(self._wait([(True, "1"), (False, ""), (True, "1"), (True, "1")]))

    def test_one_good_reading_between_drops_is_not_enough(self):
        self.assertFalse(self._wait([(True, "1"), (False, ""), (True, "1"), (False, "")]))


class OverlayDropTests(unittest.TestCase):
    def _run(self, avd_already_running: bool):
        ready = unittest.mock.Mock()
        with patch.object(start_iisu_pc, "is_avd_running", return_value=avd_already_running), \
             patch.object(start_iisu_pc, "compute_boot_fingerprint_parts", return_value={}), \
             patch.object(start_iisu_pc, "load_saved_boot_fingerprint_parts", return_value={}), \
             patch.object(start_iisu_pc, "describe_boot_fingerprint_diff", return_value=[]), \
             patch.object(start_iisu_pc, "start_avd", return_value=1234), \
             patch.object(start_iisu_pc, "save_boot_fingerprint"), \
             patch.object(start_iisu_pc, "compute_boot_fingerprint", return_value=""), \
             patch.object(start_iisu_pc, "wait_for_android_boot", return_value=True) as wait, \
             patch.object(start_iisu_pc.emulator_profiles, "active_profile", return_value={}), \
             patch.object(start_iisu_pc, "sync_rom_library", side_effect=lambda: ready.assert_called_once()), \
             patch.object(start_iisu_pc, "is_port_open", return_value=True), \
             patch.object(start_iisu_pc, "save_state"), \
             patch.object(start_iisu_pc, "launch_iisu"), \
             patch.object(start_iisu_pc, "show_iisu_window"):
            start_iisu_pc._run_start_sequence(
                {"iisu_fullscreen": True}, "iisuwin", 7737, False, {}, False, on_android_ready=ready
            )
        return ready, wait

    def test_overlay_drops_after_boot_and_before_the_rom_sync(self):
        ready, wait = self._run(avd_already_running=False)
        wait.assert_called_once()
        ready.assert_called_once()

    def test_overlay_drops_right_away_when_the_vm_was_already_up(self):
        ready, wait = self._run(avd_already_running=True)
        wait.assert_not_called()
        ready.assert_called_once()


class OverlayLaunchArgsTests(unittest.TestCase):
    def test_overlay_is_told_its_parent_and_a_time_limit(self):
        from bridge.ui import boot_overlay_qt
        with patch.object(boot_overlay_qt.subprocess, "Popen") as popen:
            boot_overlay_qt.show("Booting...", max_seconds=42)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--parent-pid") + 1], str(start_iisu_pc.os.getpid()))
        self.assertEqual(argv[argv.index("--max-seconds") + 1], "42")


if __name__ == "__main__":
    unittest.main()
