"""
Unit tests for bridge/launch_bridge.py's iiSU launch at bridge start: it waits
for the boot animation to end, retries `am start` until Android accepts it, and
logs how long both took. adb is patched and time is a fake clock, so this needs
no VM. Imports launch_bridge, which pulls in the Windows window helpers.
"""

import subprocess
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


def _done(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class LaunchIisuTests(unittest.TestCase):
    def _launch(self, getprop_answers, am_start_answers):
        clock = FakeTime()
        getprop = iter(getprop_answers)
        am_start = iter(am_start_answers)

        def run(args, **_kwargs):
            if "getprop" in args:
                return _done(next(getprop))
            return next(am_start)

        with patch.object(launch_bridge, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)), \
             patch.object(launch_bridge.subprocess, "run", side_effect=run), \
             patch.object(launch_bridge, "_is_waydroid", return_value=False), \
             patch.object(launch_bridge, "set_volume_max") as volume, \
             patch("builtins.print") as printed:
            launch_bridge.launch_iisu({"iisu_component": "com.iisulauncher/.launcher.X"})
        return [call.args[0] for call in printed.call_args_list], volume

    def test_waits_for_the_boot_animation_then_reports_the_attempt(self):
        lines, volume = self._launch(
            ["running", "running", "stopped"],
            [_done("Error: Activity class does not exist."), _done("Starting: Intent { }")],
        )
        self.assertIn("boot animation wait 2.0s", lines[0])
        self.assertIn("am start attempt 2", lines[0])
        self.assertIn("iiSU started 3.0s after asking", lines[0])
        volume.assert_called_once()

    def test_gives_up_after_sixty_attempts(self):
        failures = [_done("Error: no", returncode=1)] * 60
        lines, volume = self._launch(["stopped"], failures)
        self.assertTrue(lines[0].startswith("[bridge] could not launch iiSU"))
        volume.assert_not_called()


if __name__ == "__main__":
    unittest.main()
