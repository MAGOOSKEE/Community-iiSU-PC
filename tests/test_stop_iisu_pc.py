"""
Unit tests for bridge/stop_iisu_pc.py: reading the saved state and config,
clearing stale AVD lock files (with a retry for a lock Windows hasn't released
yet), and the commands that kill a process tree or sweep by command line.
Temporary folders and patched subprocess calls only; nothing is killed.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import stop_iisu_pc as stop  # noqa: E402; path set up above


class _TempFolderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _patch(self, name, value):
        patcher = patch.object(stop, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)


class LoadStateAndConfigTests(_TempFolderTestCase):
    def setUp(self):
        super().setUp()
        self._patch("STATE_PATH", self.root / "state.json")
        self._patch("CONFIG_PATH", self.root / "config.json")

    def test_state_missing_or_corrupt_is_empty(self):
        self.assertEqual(stop.load_state(), {})
        stop.STATE_PATH.write_text("{nope", encoding="utf-8")
        self.assertEqual(stop.load_state(), {})

    def test_state_is_read(self):
        stop.STATE_PATH.write_text(json.dumps({"emulator_pid": 42}), encoding="utf-8")
        self.assertEqual(stop.load_state(), {"emulator_pid": 42})

    def test_config_missing_or_corrupt_is_empty(self):
        self.assertEqual(stop.load_config_safely(), {})
        stop.CONFIG_PATH.write_text("{nope", encoding="utf-8")
        self.assertEqual(stop.load_config_safely(), {})

    def test_the_avd_name_in_the_state_wins_over_the_config(self):
        stop.CONFIG_PATH.write_text(json.dumps({"avd_name": "from_config"}), encoding="utf-8")
        self.assertEqual(stop.load_avd_name({"avd_name": "from_state"}), "from_state")

    def test_the_avd_name_falls_back_to_the_config_then_none(self):
        self.assertIsNone(stop.load_avd_name({}))
        stop.CONFIG_PATH.write_text(json.dumps({"avd_name": "from_config"}), encoding="utf-8")
        self.assertEqual(stop.load_avd_name({}), "from_config")


class RemovePathWithRetryTests(_TempFolderTestCase):
    def test_removes_a_file_and_an_empty_folder(self):
        file = self.root / "a.lock"
        file.write_bytes(b"x")
        folder = self.root / "b.lock"
        folder.mkdir()
        stop._remove_path_with_retry(file)
        stop._remove_path_with_retry(folder)
        self.assertFalse(file.exists())
        self.assertFalse(folder.exists())

    def test_a_path_that_is_already_gone_is_fine(self):
        stop._remove_path_with_retry(self.root / "missing.lock")

    def test_it_retries_a_locked_file_then_gives_up_without_raising(self):
        file = self.root / "stuck.lock"
        file.write_bytes(b"x")
        with patch.object(Path, "unlink", side_effect=PermissionError("locked")) as unlink, \
             patch.object(stop.time, "sleep") as sleep:
            stop._remove_path_with_retry(file, attempts=3, delay=0.5)
        self.assertEqual(unlink.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(file.exists())

    def test_it_succeeds_when_the_lock_is_released_in_time(self):
        file = self.root / "slow.lock"
        file.write_bytes(b"x")
        real_unlink = Path.unlink
        calls = {"n": 0}

        def flaky(path, missing_ok=False):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked")
            return real_unlink(path, missing_ok=missing_ok)

        with patch.object(Path, "unlink", flaky), patch.object(stop.time, "sleep"):
            stop._remove_path_with_retry(file, attempts=5, delay=0.1)
        self.assertFalse(file.exists())
        self.assertEqual(calls["n"], 3)


class ClearStaleLocksTests(_TempFolderTestCase):
    def setUp(self):
        super().setUp()
        self._patch("PORTABLE_AVD_HOME", self.root)
        self.avd = self.root / "iisuwin.avd"
        self.avd.mkdir()

    def test_removes_lock_files_and_lock_folders_but_nothing_else(self):
        (self.avd / "hardware-qemu.ini.lock").write_bytes(b"x")
        folder = self.avd / "multiinstance.lock"
        folder.mkdir()
        (folder / "pid").write_bytes(b"1")
        (self.avd / "config.ini").write_bytes(b"keep")
        (self.avd / "userdata-qemu.img.qcow2").write_bytes(b"keep")
        stop.clear_stale_locks("iisuwin")
        self.assertEqual(sorted(p.name for p in self.avd.iterdir()), ["config.ini", "userdata-qemu.img.qcow2"])

    def test_no_avd_name_or_no_folder_does_nothing(self):
        stop.clear_stale_locks(None)
        stop.clear_stale_locks("")
        stop.clear_stale_locks("other")
        self.assertTrue(self.avd.is_dir())


class KillCommandTests(unittest.TestCase):
    def test_windows_kill_tree_uses_taskkill_on_the_whole_tree(self):
        with patch.object(stop, "IS_WINDOWS", True), patch.object(stop.subprocess, "run") as run:
            stop.kill_tree(1234)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "1234", "/T", "/F"])

    def test_the_command_line_sweep_on_windows_matches_by_command_line(self):
        with patch.object(stop, "IS_WINDOWS", True), patch.object(stop.subprocess, "run") as run:
            stop.kill_by_cmdline_match("launch_bridge.py")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell")
        self.assertIn("-like '*launch_bridge.py*'", command[-1])
        self.assertIn("Stop-Process", command[-1])

    def test_the_command_line_sweep_off_windows_uses_pkill(self):
        with patch.object(stop, "IS_WINDOWS", False), patch.object(stop.subprocess, "run") as run:
            stop.kill_by_cmdline_match("launch_bridge.py")
        self.assertEqual(run.call_args.args[0], ["pkill", "-f", "launch_bridge.py"])


class StopWaydroidTests(unittest.TestCase):
    def test_a_failure_is_reported_not_raised(self):
        import waydroid_backend

        with patch.object(waydroid_backend, "stop", side_effect=RuntimeError("no session")):
            stop.stop_waydroid()


if __name__ == "__main__":
    unittest.main()
