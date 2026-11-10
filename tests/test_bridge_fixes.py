"""Small launch bridge fixes: the shutdown hotkey's key lookup, and asking the
system (not tasklist.exe) whether a game's process is still running."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import launch_bridge  # noqa: E402
import winapi  # noqa: E402


class ShutdownHotkeyTests(unittest.TestCase):
    def _registered_key(self, hotkey):
        with patch.object(launch_bridge.user32, "RegisterHotKey", return_value=1) as register, \
             patch("builtins.print"):
            label = launch_bridge._register_hotkey(hotkey, 2, "full shutdown")
        return (register.call_args.args[3] if register.called else None), label

    def test_a_named_key_registers_that_key_not_its_first_letter(self):
        self.assertEqual(self._registered_key({"modifiers": [], "key": "f10"}), (0x79, "F10"))
        self.assertEqual(self._registered_key({"modifiers": ["ctrl"], "key": "escape"})[0], 0x1B)

    def test_a_letter_key_is_unchanged(self):
        self.assertEqual(self._registered_key({"modifiers": ["ctrl", "alt"], "key": "x"}), (ord("X"), "CTRL+ALT+X"))

    def test_no_key_registers_nothing(self):
        self.assertEqual(self._registered_key({"modifiers": ["ctrl"], "key": ""}), (None, None))


class PidIsRunningTests(unittest.TestCase):
    def test_this_process_is_running(self):
        self.assertTrue(winapi.pid_is_running(os.getpid()))

    def test_no_pid_is_not_running(self):
        self.assertFalse(winapi.pid_is_running(None))
        self.assertFalse(winapi.pid_is_running(0))

    def test_a_process_that_has_exited_is_not_running_even_while_still_held(self):
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait()
        # Popen still holds the process open here, so its ID can't be reused.
        self.assertFalse(winapi.pid_is_running(process.pid))

    def test_the_bridge_never_starts_tasklist(self):
        with patch.object(launch_bridge.subprocess, "run") as run:
            launch_bridge._pid_is_running(os.getpid())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
