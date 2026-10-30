"""
Unit tests for bridge/start_iisu_pc.py's boot-fingerprint diffing and the
quickboot-autosave regression: set_quickboot_autosave(enabled=not
effective_cold_boot) meant a cold boot, which includes every first run,
and any run after a settings/ROM-library change, never saved a snapshot
to resume *from*, so the very next start's "quick resume" was never
backed by anything real. No AVD or adb needed for either.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import portable_sdk  # noqa: E402; path set up above
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


class FingerprintScopeTests(unittest.TestCase):
    """Only what changes the VM's hardware or launch belongs in the fingerprint:
    anything else in it forces a cold boot for nothing."""

    BASE = {"avd_name": "iisuwin", "display": {"width": 1920}, "emulators": {"a": {"exe_names": ["a.exe"]}},
            "usb_passthrough": [], "roms_dir": "C:/roms-a"}

    def _diff(self, **changes):
        changed = {**self.BASE, **changes}
        return start_iisu_pc.describe_boot_fingerprint_diff(
            start_iisu_pc.compute_boot_fingerprint_parts(self.BASE),
            start_iisu_pc.compute_boot_fingerprint_parts(changed),
        )

    def test_changing_the_pc_emulator_mappings_still_resumes(self):
        self.assertEqual(self._diff(emulators={"b": {"exe_names": ["b.exe"]}}), [])

    def test_changing_the_rom_library_still_resumes(self):
        self.assertEqual(self._diff(roms_dir="D:/roms-b"), [])

    def test_hardware_changes_still_cold_boot(self):
        self.assertEqual(self._diff(display={"width": 2560}), ["the display settings changed"])
        self.assertEqual(self._diff(usb_passthrough=[{"vid": "1"}]), ["the USB passthrough list changed"])

    def test_a_fingerprint_saved_with_the_old_parts_still_resumes(self):
        old = {**start_iisu_pc.compute_boot_fingerprint_parts(self.BASE), "emulators": "x", "roms_dir": "y"}
        self.assertEqual(
            start_iisu_pc.describe_boot_fingerprint_diff(old, start_iisu_pc.compute_boot_fingerprint_parts(self.BASE)), [],
        )


class BootModeFingerprintTests(unittest.TestCase):
    CONFIG = {"avd_name": "iisuwin", "display": {}, "emulators": {}, "usb_passthrough": [], "roms_dir": "C:/nonexistent"}

    def test_the_boot_mode_is_part_of_the_fingerprint(self):
        windowed = start_iisu_pc.compute_boot_fingerprint_parts(self.CONFIG)
        headless = start_iisu_pc.compute_boot_fingerprint_parts(self.CONFIG, headless=True)
        self.assertEqual(windowed["boot_mode"], "windowed")
        self.assertEqual(headless["boot_mode"], "headless")
        self.assertNotEqual(
            start_iisu_pc.compute_boot_fingerprint(self.CONFIG), start_iisu_pc.compute_boot_fingerprint(self.CONFIG, headless=True)
        )

    def test_a_swap_in_either_direction_is_reported(self):
        windowed = start_iisu_pc.compute_boot_fingerprint_parts(self.CONFIG)
        headless = start_iisu_pc.compute_boot_fingerprint_parts(self.CONFIG, headless=True)
        expected = ["the boot mode (window or headless) changed"]
        self.assertEqual(start_iisu_pc.describe_boot_fingerprint_diff(windowed, headless), expected)
        self.assertEqual(start_iisu_pc.describe_boot_fingerprint_diff(headless, windowed), expected)

    def test_a_fingerprint_saved_before_the_part_existed_counts_as_windowed(self):
        windowed = start_iisu_pc.compute_boot_fingerprint_parts(self.CONFIG)
        old = {key: value for key, value in windowed.items() if key != "boot_mode"}
        self.assertEqual(start_iisu_pc.describe_boot_fingerprint_diff(old, windowed), [])
        self.assertFalse(start_iisu_pc.boot_mode_changed(old, windowed))

    def test_boot_mode_changed(self):
        windowed = start_iisu_pc.compute_boot_fingerprint_parts(self.CONFIG)
        headless = start_iisu_pc.compute_boot_fingerprint_parts(self.CONFIG, headless=True)
        self.assertTrue(start_iisu_pc.boot_mode_changed(windowed, headless))
        self.assertTrue(start_iisu_pc.boot_mode_changed(headless, windowed))
        self.assertFalse(start_iisu_pc.boot_mode_changed(headless, headless))
        self.assertFalse(start_iisu_pc.boot_mode_changed(None, headless))


class QuickbootAutosaveRegressionTests(unittest.TestCase):
    """start_avd() must always leave quickboot autosave ON before launching,
    regardless of whether this particular boot is cold or a resume, the
    boot-fingerprint check is what decides whether a saved snapshot gets
    trusted later; autosave being off would mean there's never one to
    trust in the first place."""

    def _run_start_avd(self, force_cold_boot: bool, headless: bool = False):
        # clear_quickboot_snapshot is patched: unpatched it would delete this
        # machine's real snapshot under PORTABLE_AVD_HOME.
        with patch.object(start_iisu_pc, "find_system_emulator_exe", return_value=Path("C:/fake/emulator.exe")), \
             patch.object(start_iisu_pc, "ensure_portable_sdk", return_value={"ANDROID_SDK_ROOT": "C:/fake/sdk"}), \
             patch.object(start_iisu_pc, "clear_stale_locks"), \
             patch.object(start_iisu_pc, "patch_config_ini") as config_ini, \
             patch.object(start_iisu_pc, "clear_quickboot_snapshot") as cleared, \
             patch.object(start_iisu_pc, "set_quickboot_autosave") as mock_autosave, \
             patch.object(start_iisu_pc, "_launch_once", return_value=12345):
            pid = start_iisu_pc.start_avd("iisuwin", [], force_cold_boot=force_cold_boot, headless=headless)
            self.assertEqual(pid, 12345)
            self.config_ini, self.cleared = config_ini, cleared
            return mock_autosave

    def test_a_windowed_cold_boot_deletes_the_snapshot_and_boots_like_a_quick_boot(self):
        # -no-snapshot-load and forceColdBoot=yes save nothing on exit without an
        # existing complete snapshot, so the start after them cold booted again.
        self._run_start_avd(force_cold_boot=True)
        self.cleared.assert_called_once()
        self.assertIs(self.config_ini.call_args.kwargs["force_cold_boot"], False)

    def test_a_resume_keeps_the_snapshot(self):
        self._run_start_avd(force_cold_boot=False)
        self.cleared.assert_not_called()
        self.assertIs(self.config_ini.call_args.kwargs["force_cold_boot"], False)

    def test_a_headless_cold_boot_keeps_forcing_it_in_the_config(self):
        self._run_start_avd(force_cold_boot=True, headless=True)
        self.cleared.assert_not_called()
        self.assertIs(self.config_ini.call_args.kwargs["force_cold_boot"], True)

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
            start_iisu_pc._run_start_sequence({"iisu_fullscreen": True, "vm_backend": "avd"}, "iisuwin", 7737, False, {}, headless)
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


class HeadlessWithWaydroidTests(unittest.TestCase):
    def test_headless_is_ignored_for_waydroid(self):
        config = {"vm_backend": "waydroid", "avd_name": "iisuwin", "bridge_port": 7737}
        with patch.object(start_iisu_pc, "load_config", return_value=config), \
             patch.object(start_iisu_pc, "resolve_backend", return_value=start_iisu_pc.BACKEND_WAYDROID), \
             patch.object(start_iisu_pc, "supports_headless_boot", return_value=False), \
             patch.object(start_iisu_pc.winapi, "ensure_linux_kwin_rules"), \
             patch.object(start_iisu_pc.iisu_install_record, "needs_repatch", return_value=False), \
             patch.object(start_iisu_pc, "_run_start_sequence") as sequence, \
             patch.object(start_iisu_pc.boot_overlay, "close"):
            start_iisu_pc.main(headless=True)
        self.assertIs(sequence.call_args.args[5], False, "the headless argument")


class RepatchGateTests(unittest.TestCase):
    def test_an_older_patch_opens_the_manager_instead_of_starting(self):
        # The desktop shortcut runs this script directly, past the Manager's own check.
        config = {"vm_backend": "avd", "avd_name": "iisuwin", "bridge_port": 7737}
        with patch.object(start_iisu_pc, "load_config", return_value=config), \
             patch.object(start_iisu_pc.winapi, "ensure_linux_kwin_rules"), \
             patch.object(start_iisu_pc.iisu_install_record, "needs_repatch", return_value=True), \
             patch.object(start_iisu_pc, "open_manager") as manager, \
             patch.object(start_iisu_pc, "_run_start_sequence") as sequence, \
             patch("builtins.print"):
            with self.assertRaises(SystemExit):
                start_iisu_pc.main()
        manager.assert_called_once()
        sequence.assert_not_called()


class HeadlessSnapshotTests(unittest.TestCase):
    """A snapshot is tied to how it was taken (host GPU or software rendering, with or without a
    window) and to the disk it was taken from, so every swap between a windowed and a headless
    start must cold boot and delete it, in both directions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.avd_home = Path(self._tmp.name)
        self.snapshot = self.avd_home / "iisuwin.avd" / "snapshots" / "default_boot"
        self.snapshot.mkdir(parents=True)
        for name in portable_sdk.QUICKBOOT_SNAPSHOT_FILES:
            (self.snapshot / name).write_bytes(b"x")

    def _start(self, headless: bool, previous_mode: str | None):
        """previous_mode is the boot mode the saved fingerprint records, None for no saved fingerprint."""
        def parts(config, headless=False):
            return {"a": "1", "boot_mode": "headless" if headless else "windowed"}

        saved = None if previous_mode is None else {"a": "1", "boot_mode": previous_mode}
        with patch.object(start_iisu_pc, "is_avd_running", return_value=False), \
             patch.object(start_iisu_pc, "PORTABLE_AVD_HOME", self.avd_home), \
             patch.object(start_iisu_pc, "compute_boot_fingerprint_parts", side_effect=parts), \
             patch.object(start_iisu_pc, "load_saved_boot_fingerprint_parts", return_value=saved), \
             patch.object(start_iisu_pc, "compute_boot_fingerprint", return_value="fp") as fingerprint, \
             patch.object(start_iisu_pc, "start_avd", return_value=4242) as start_avd, \
             patch.object(start_iisu_pc, "save_boot_fingerprint") as save, \
             patch.object(start_iisu_pc, "sync_rom_library"), \
             patch.object(start_iisu_pc, "save_state"), \
             patch.object(start_iisu_pc, "is_port_open", return_value=True), \
             patch.object(start_iisu_pc, "launch_iisu"), \
             patch.object(start_iisu_pc, "show_iisu_window"):
            start_iisu_pc._run_start_sequence({"vm_backend": "avd"}, "iisuwin", 7737, False, {}, headless)
        return start_avd, save, fingerprint

    def test_headless_always_cold_boots_and_deletes_the_snapshot(self):
        start_avd, _, _ = self._start(headless=True, previous_mode="headless")
        self.assertIs(start_avd.call_args.kwargs["force_cold_boot"], True)
        self.assertFalse(self.snapshot.exists())

    def test_windowed_to_headless_deletes_the_snapshot(self):
        start_avd, _, _ = self._start(headless=True, previous_mode="windowed")
        self.assertIs(start_avd.call_args.kwargs["force_cold_boot"], True)
        self.assertFalse(self.snapshot.exists())

    def test_headless_to_windowed_cold_boots_and_deletes_a_leftover_snapshot(self):
        start_avd, _, _ = self._start(headless=False, previous_mode="headless")
        self.assertIs(start_avd.call_args.kwargs["force_cold_boot"], True)
        self.assertFalse(self.snapshot.exists())

    def test_a_headless_start_records_its_mode_for_the_next_start(self):
        _, save, fingerprint = self._start(headless=True, previous_mode="windowed")
        save.assert_called_once()
        self.assertEqual(save.call_args.args[1]["boot_mode"], "headless")
        self.assertIs(fingerprint.call_args.args[1], True)

    def test_an_incomplete_snapshot_is_cold_booted_so_a_whole_one_gets_saved(self):
        # What a save that never happened leaves behind: resuming it cold boots
        # without saving, so every later start cold booted too.
        for name in ("hardware.ini", "ram.bin"):
            (self.snapshot / name).unlink()
        with patch("builtins.print") as printed:
            start_avd, _, _ = self._start(headless=False, previous_mode="windowed")
        self.assertIs(start_avd.call_args.kwargs["force_cold_boot"], True)
        self.assertTrue(any(start_iisu_pc.INCOMPLETE_SNAPSHOT_REASON in str(call) for call in printed.call_args_list))

    def test_a_normal_start_after_a_normal_start_still_resumes_and_keeps_the_snapshot(self):
        start_avd, save, _ = self._start(headless=False, previous_mode="windowed")
        self.assertIs(start_avd.call_args.kwargs["force_cold_boot"], False)
        self.assertTrue(self.snapshot.exists())
        save.assert_called_once()
        self.assertEqual(save.call_args.args[1]["boot_mode"], "windowed")

    def test_the_log_ends_with_how_long_each_stage_took(self):
        with patch("builtins.print") as printed:
            self._start(headless=True, previous_mode="headless")
        lines = [call.args[0] for call in printed.call_args_list]
        took = next(line for line in lines if line.startswith("[start] Took "))
        self.assertIn("Android VM", took)
        self.assertIn("ROM library", took)
        self.assertTrue(lines[-1].startswith("[start] Ready in "))
        self.assertTrue(any(" is up (headless, no window) after " in line for line in lines))

    def test_a_first_ever_windowed_start_does_not_touch_the_snapshot(self):
        start_avd, _, _ = self._start(headless=False, previous_mode=None)
        self.assertIs(start_avd.call_args.kwargs["force_cold_boot"], True)
        self.assertTrue(self.snapshot.exists())


class FakeClock:
    """A stand-in for the time module that only moves when something sleeps."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    @staticmethod
    def strftime(*args):
        return "now"


class FakeProcess:
    """An emulator process that exits with `code` once `lives` seconds have passed on `clock`
    (never, when `code` is None)."""

    pid = 4242

    def __init__(self, clock, lives=None, code=None):
        self.clock = clock
        self.exit_at = None if lives is None else clock.now + lives
        self.code = code

    def poll(self):
        if self.exit_at is not None and self.clock.now >= self.exit_at:
            return self.code
        return None

    @property
    def returncode(self):
        return self.code if self.poll() is not None else None


class HeadlessSettleTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = patch.object(start_iisu_pc, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_process_that_keeps_running_counts_as_up(self):
        self.assertTrue(start_iisu_pc._stays_up(FakeProcess(self.clock), 30))
        self.assertGreaterEqual(self.clock.now, 1030)

    def test_a_crash_during_the_wait_is_reported_with_its_exit_code(self):
        process = FakeProcess(self.clock, lives=13, code=0xC0000005)
        with patch("builtins.print") as printed:
            self.assertFalse(start_iisu_pc._stays_up(process, 30))
        self.assertIn("0xC0000005", printed.call_args_list[0].args[0])
        self.assertLess(self.clock.now, 1030, "it stops waiting as soon as the process is gone")

    def test_a_negative_exit_code_is_shown_as_unsigned(self):
        process = FakeProcess(self.clock, lives=1, code=-1073741819)
        with patch("builtins.print") as printed:
            start_iisu_pc._stays_up(process, 30)
        self.assertIn("0xC0000005", printed.call_args_list[0].args[0])

    def _launch(self, headless: bool, process: FakeProcess, android_ui_up=True):
        """android_ui_up: what the on-device wait for the boot animation answers,
        or a number of seconds it takes before the process (maybe) dies."""
        profile = start_iisu_pc.emulator_profiles.normalize_profile({})

        def wait_for_ui(_seconds):
            return android_ui_up

        with patch.object(start_iisu_pc.subprocess, "Popen", return_value=process), \
             patch.object(start_iisu_pc, "is_avd_running", return_value=True), \
             patch.object(start_iisu_pc, "_wait_for_android_ui", side_effect=wait_for_ui) as ui_wait, \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("builtins.print") as printed:
            self.ui_wait = ui_wait
            self.printed = printed
            return start_iisu_pc._launch_once(Path("emulator.exe"), "iisuwin", {}, [], False, profile, False, headless)

    def _lines(self):
        return [call.args[0] for call in self.printed.call_args_list]

    def test_a_headless_start_whose_emulator_dies_right_after_boot_fails(self):
        self.assertIsNone(self._launch(True, FakeProcess(self.clock, lives=5, code=0xC0000005)))
        self.assertTrue(any("0xC0000005" in line for line in self._lines()))

    def test_a_headless_start_that_stays_up_returns_the_pid(self):
        self.assertEqual(self._launch(True, FakeProcess(self.clock)), 4242)
        self.assertGreaterEqual(self.clock.now, 1000 + start_iisu_pc.HEADLESS_SETTLE_SECONDS)
        self.ui_wait.assert_called_once()

    def test_a_crash_while_android_starts_is_reported_with_its_exit_code(self):
        process = FakeProcess(self.clock, lives=0, code=0xC0000005)
        self.assertIsNone(self._launch(True, process, android_ui_up=False))
        self.assertTrue(any("0xC0000005" in line for line in self._lines()))

    def test_android_not_finishing_its_boot_is_reported(self):
        self.assertIsNone(self._launch(True, FakeProcess(self.clock), android_ui_up=False))
        self.assertIn("[start] Android did not finish starting in time.", self._lines())

    def test_a_windowed_start_does_not_wait(self):
        self.assertEqual(self._launch(False, FakeProcess(self.clock, lives=13, code=0xC0000005)), 4242)
        self.assertEqual(self.clock.now, 1000.0)
        self.ui_wait.assert_not_called()


class WaitForAndroidUiTests(unittest.TestCase):
    def test_the_device_waits_for_the_boot_animation_within_the_limit(self):
        with patch.object(start_iisu_pc.adb_client, "shell", return_value=(0, "")) as shell:
            self.assertTrue(start_iisu_pc._wait_for_android_ui(42.7))
        script = shell.call_args.args[0]
        self.assertIn("+ 42 ))", script)
        self.assertIn("init.svc.bootanim", script)
        self.assertNotIn("boot_completed", script)

    def test_a_timeout_or_lost_device_is_false(self):
        for answer in ((1, ""), (None, "")):
            with self.subTest(answer=answer), patch.object(start_iisu_pc.adb_client, "shell", return_value=answer):
                self.assertFalse(start_iisu_pc._wait_for_android_ui(10))

    def test_a_spent_deadline_still_checks_once(self):
        with patch.object(start_iisu_pc.adb_client, "shell", return_value=(0, "")) as shell:
            start_iisu_pc._wait_for_android_ui(-5)
        self.assertIn("+ 1 ))", shell.call_args.args[0])


class HeadlessLaunchTests(unittest.TestCase):
    def _emulator_args(self, headless: bool, force_cold_boot: bool = False) -> list[str]:
        """The command line _launch_once builds, without starting anything."""
        profile = start_iisu_pc.emulator_profiles.normalize_profile({})
        clock = FakeClock()
        with patch.object(start_iisu_pc, "time", clock), \
             patch.object(start_iisu_pc.subprocess, "Popen", return_value=FakeProcess(clock)) as popen, \
             patch.object(start_iisu_pc, "is_avd_running", return_value=True), \
             patch.object(start_iisu_pc, "_wait_for_android_ui", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open()):
            start_iisu_pc._launch_once(Path("emulator.exe"), "iisuwin", {}, [], False, profile, force_cold_boot, headless)
            return popen.call_args.args[0]

    def test_a_windowed_cold_boot_passes_no_snapshot_flag(self):
        # Every -no-snapshot* flag stops the save on exit; start_avd deletes the
        # snapshot instead, so the launch itself is a plain quick boot.
        args = self._emulator_args(headless=False, force_cold_boot=True)
        self.assertFalse([arg for arg in args if arg.startswith("-no-snapshot")])

    def test_a_resume_passes_no_snapshot_flag(self):
        args = self._emulator_args(headless=False, force_cold_boot=False)
        self.assertFalse([arg for arg in args if arg.startswith("-no-snapshot")])

    def test_a_headless_cold_boot_neither_loads_nor_saves(self):
        args = self._emulator_args(headless=True, force_cold_boot=True)
        self.assertIn("-no-snapshot", args)
        self.assertNotIn("-no-snapshot-load", args)

    def test_headless_adds_no_window(self):
        self.assertIn("-no-window", self._emulator_args(headless=True))

    def test_a_normal_start_keeps_the_window(self):
        self.assertNotIn("-no-window", self._emulator_args(headless=False))

    def test_headless_on_windows_uses_the_host_gpu_and_no_audio(self):
        with patch.object(start_iisu_pc, "IS_WINDOWS", True):
            args = self._emulator_args(headless=True)
        self.assertEqual(args[args.index("-gpu") + 1], "host")
        self.assertIn("-no-audio", args)

    def test_headless_elsewhere_keeps_software_rendering(self):
        with patch.object(start_iisu_pc, "IS_WINDOWS", False):
            args = self._emulator_args(headless=True)
        self.assertEqual(args[args.index("-gpu") + 1], "swiftshader_indirect")

    def test_headless_never_uses_gpu_auto_which_means_software_without_a_window(self):
        for is_windows in (True, False):
            with self.subTest(is_windows=is_windows), patch.object(start_iisu_pc, "IS_WINDOWS", is_windows):
                args = self._emulator_args(headless=True)
                self.assertNotEqual(args[args.index("-gpu") + 1], "auto")

    def test_a_normal_start_keeps_the_profile_gpu_and_audio(self):
        args = self._emulator_args(headless=False)
        self.assertEqual(args[args.index("-gpu") + 1], "auto")
        self.assertNotIn("-no-audio", args)

    def test_start_avd_passes_headless_through_every_attempt(self):
        with patch.object(start_iisu_pc, "find_system_emulator_exe", return_value=Path("C:/fake/emulator.exe")), \
             patch.object(start_iisu_pc, "ensure_portable_sdk", return_value={"ANDROID_SDK_ROOT": "C:/fake/sdk"}), \
             patch.object(start_iisu_pc, "clear_stale_locks"), \
             patch.object(start_iisu_pc, "patch_config_ini"), \
             patch.object(start_iisu_pc, "clear_quickboot_snapshot"), \
             patch.object(start_iisu_pc, "set_quickboot_autosave"), \
             patch.object(start_iisu_pc, "_launch_once", return_value=1) as launch:
            start_iisu_pc.start_avd("iisuwin", [], headless=True)
        self.assertIs(launch.call_args.args[-1], True)


if __name__ == "__main__":
    unittest.main()
