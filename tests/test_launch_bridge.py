"""
Unit tests for bridge/launch_bridge.py's adb use: launching iiSU at bridge
start (one on-device wait for the boot animation, then `am start` retried until
Android accepts it, with each stage timed as its marker arrives) and the media
volume helpers used at every game launch. The adb client is patched and time is
a fake clock, so this needs no VM. Imports launch_bridge, which pulls in the
Windows window helpers.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bridge"))

import launch_bridge  # noqa: E402; path set up above


class FakeTime:
    def __init__(self):
        self.now = 50.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class LaunchIisuTests(unittest.TestCase):
    def _launch(self, events, waydroid=False):
        """events: (seconds that pass on the device, line it prints) pairs."""
        clock = FakeTime()
        scripts = []

        def shell_lines(script, timeout):
            scripts.append((script, timeout))
            for seconds, line in events:
                clock.now += seconds
                yield line

        with patch.object(launch_bridge, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)), \
             patch.object(launch_bridge.adb_client, "shell_lines", side_effect=shell_lines), \
             patch.object(launch_bridge, "_is_waydroid", return_value=waydroid), \
             patch.object(launch_bridge, "set_volume_max") as volume, \
             patch("builtins.print") as printed:
            launch_bridge.launch_iisu({"iisu_component": "com.iisulauncher/.launcher.X"})
        return [call.args[0] for call in printed.call_args_list], volume, scripts

    def test_times_each_stage_as_its_marker_arrives(self):
        lines, volume, _ = self._launch([
            (2.0, "bootanim_done"), (1.0, "started_attempt=2"), (0.0, "__iisupc_exit=0"),
        ])
        self.assertIn("iiSU started 3.0s after asking", lines[0])
        self.assertIn("boot animation wait 2.0s", lines[0])
        self.assertIn("am start attempt 2", lines[0])
        volume.assert_called_once()

    def test_a_failed_start_reports_what_android_said(self):
        lines, volume, _ = self._launch([
            (0.5, "bootanim_done"), (60.0, "Error: Activity class {com.iisulauncher/.launcher.X} does not exist."),
            (0.0, "__iisupc_exit=1"),
        ])
        self.assertTrue(lines[0].startswith("[bridge] could not launch iiSU"))
        self.assertIn("does not exist", lines[1])
        volume.assert_not_called()

    def test_no_answer_from_adb_is_reported(self):
        lines, volume, _ = self._launch([])
        self.assertIn("no answer from adb", lines[1])
        volume.assert_not_called()

    def test_the_device_script_carries_the_component_and_the_waits(self):
        _, _, scripts = self._launch([(0, "__iisupc_exit=1")])
        script, timeout = scripts[0]
        self.assertIn("am start -n com.iisulauncher/.launcher.X", script)
        self.assertIn(f"+ {launch_bridge.BOOTANIM_WAIT_SECONDS} ))", script)
        self.assertIn(f"+ {launch_bridge.IISU_START_WAIT_SECONDS} ))", script)
        self.assertGreater(timeout, launch_bridge.BOOTANIM_WAIT_SECONDS + launch_bridge.IISU_START_WAIT_SECONDS)

    def test_waydroid_does_not_wait_for_a_boot_animation(self):
        _, _, scripts = self._launch([(0, "__iisupc_exit=1")], waydroid=True)
        self.assertIn("+ 0 ))", scripts[0][0])


class QuitTapTests(unittest.TestCase):
    def test_quitting_a_subprocess_emulator_is_logged_and_terminates_it(self):
        proc = SimpleNamespace(pid=4321, poll=lambda: None, terminated=False)
        proc.terminate = lambda: setattr(proc, "terminated", True)
        with patch.object(launch_bridge, "current_process", proc), \
             patch.object(launch_bridge, "current_native_window", None), \
             patch.object(launch_bridge, "current_native_pid", None), \
             patch.object(launch_bridge, "game_handoff_active", False), \
             patch.object(launch_bridge, "log_launch") as log, \
             patch.object(launch_bridge, "shutdown_everything") as shutdown, \
             patch("builtins.print"):
            launch_bridge.quit_tap_action("escape")
        self.assertTrue(proc.terminated)
        shutdown.assert_not_called()
        log.assert_called_once_with("QUIT: escape, terminating emulator PID 4321")


class MediaVolumeTests(unittest.TestCase):
    def test_reads_the_volume_from_androids_answer(self):
        with patch.object(launch_bridge.adb_client, "shell", return_value=(0, "volume is 7 in range [0..15]")):
            self.assertEqual(launch_bridge._adb_media_volume_get(), 7)

    def test_an_unreadable_answer_is_none(self):
        with patch.object(launch_bridge.adb_client, "shell", return_value=(None, "")):
            self.assertIsNone(launch_bridge._adb_media_volume_get())

    def test_setting_reports_success_and_never_goes_negative(self):
        with patch.object(launch_bridge.adb_client, "shell", return_value=(0, "")) as shell:
            self.assertTrue(launch_bridge._adb_media_volume_set(-3))
        command = shell.call_args.args[0]
        self.assertTrue(command.startswith('case "$(cmd audio set-volume 3 0 2>&1)"'), "cmd audio is tried first")
        self.assertIn("*setStreamVolume*) exit 0", command)
        self.assertIn("--set 0 ", command)
        with patch.object(launch_bridge.adb_client, "shell", return_value=(1, "")):
            self.assertFalse(launch_bridge._adb_media_volume_set(5))


if __name__ == "__main__":
    unittest.main()
