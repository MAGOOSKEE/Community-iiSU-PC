"""Unit tests for setup_wizard.forget_resume_state: every Setup, iiSU update or
rebuild boot changes the VM's disk, so it must drop the quick-resume snapshot
and the boot fingerprint. A re-run Setup used to leave both, and the next start
tried to resume memory from before Setup. Temporary folders only."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "installer"))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import portable_sdk  # noqa: E402; path set up above
import setup_wizard  # noqa: E402; path set up above


class ForgetResumeStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.avd_home = root / "avd-home"
        self.bridge = root / "bridge"
        self.bridge.mkdir()
        self.snapshot = self.avd_home / "iisuwin.avd" / "snapshots" / "default_boot"
        self.snapshot.mkdir(parents=True)
        for name in portable_sdk.QUICKBOOT_SNAPSHOT_FILES:
            (self.snapshot / name).write_bytes(b"x")
        self.disk = self.avd_home / "iisuwin.avd" / "userdata-qemu.img.qcow2"
        self.disk.write_bytes(b"data")
        self.fingerprint = self.bridge / ".boot_fingerprint.json"
        self.fingerprint.write_text("{}", encoding="utf-8")

    def _forget(self):
        with patch.object(portable_sdk, "PORTABLE_AVD_HOME", self.avd_home), \
             patch.object(setup_wizard, "BRIDGE_DIR", self.bridge):
            setup_wizard.forget_resume_state("iisuwin")

    def test_drops_the_snapshot_and_the_fingerprint_but_not_the_disk(self):
        self._forget()
        self.assertFalse(self.snapshot.exists())
        self.assertFalse(self.fingerprint.exists())
        self.assertTrue(self.disk.is_file())

    def test_nothing_to_drop_is_fine(self):
        self._forget()
        self._forget()


class ShutDownAndroidTests(unittest.TestCase):
    """The install boot has no snapshot, so adb emu kill lost every redirector
    app Android hadn't written to disk yet."""

    def _shut_down(self, connected_answers):
        process = SimpleNamespace(poll=lambda: 0, terminate=lambda: None, wait=lambda timeout: 0)
        with patch.object(setup_wizard.subprocess, "run") as run, \
             patch.object(setup_wizard, "is_avd_connected", side_effect=connected_answers), \
             patch.object(setup_wizard.time, "sleep"), \
             patch("builtins.print"):
            setup_wizard.shut_down_android(process)
        return [call.args[0] for call in run.call_args_list]

    def test_android_is_flushed_and_powered_off_not_killed(self):
        commands = self._shut_down(lambda: False)
        self.assertEqual(commands, [["adb", "shell", "sync"], ["adb", "shell", "svc", "power", "shutdown"]])

    def test_a_power_off_that_never_finishes_falls_back_to_a_kill(self):
        clock = iter(range(0, 10_000, 5))
        with patch.object(setup_wizard.time, "time", side_effect=lambda: next(clock)):
            commands = self._shut_down(lambda: True)
        self.assertIn(["adb", "emu", "kill"], commands)


class TrimRetryTests(unittest.TestCase):
    """A trimmed VM that won't boot is rebuilt and trimmed once more before
    Setup gives up on the trim: an emulator crash had cost a whole install it."""

    def _install(self, broken_trims):
        lean_args = []
        reports = iter([SimpleNamespace(needs_rebuild=True)] * broken_trims)

        def install(_apk, lean=False, alive=None, restart=None):
            lean_args.append(lean)
            return next(reports, SimpleNamespace(needs_rebuild=False)) if lean else None

        with patch.object(setup_wizard, "is_avd_connected", return_value=True), \
             patch.object(setup_wizard, "wait_for_avd", return_value=True), \
             patch.object(setup_wizard, "install_iisu_into_running_vm", side_effect=install), \
             patch.object(setup_wizard, "stop_avd"), \
             patch.object(portable_sdk, "reset_avd_data") as rebuilt, \
             patch.object(setup_wizard, "shut_down_android"), \
             patch.object(setup_wizard, "forget_resume_state"), \
             patch("builtins.print"):
            setup_wizard.boot_avd_and_install(Path("emulator"), "iisuwin", {}, Path("iiSU.apk"), lean=True)
        return lean_args, rebuilt.call_count

    def test_a_trim_that_fails_once_is_tried_again(self):
        self.assertEqual(self._install(1), ([True, True], 1))

    def test_a_trim_that_fails_twice_is_skipped(self):
        self.assertEqual(self._install(2), ([True, True, False], 2))

    def test_a_working_trim_is_not_rebuilt(self):
        self.assertEqual(self._install(0), ([True], 0))


if __name__ == "__main__":
    unittest.main()
