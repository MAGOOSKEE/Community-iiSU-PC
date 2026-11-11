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


class ReadRequestTests(unittest.TestCase):
    class Connection:
        """Hands out the given chunks, then behaves as `then` says."""

        def __init__(self, chunks, then="closed"):
            self._chunks = list(chunks)
            self._then = then
            self.timeout = None

        def settimeout(self, seconds):
            self.timeout = seconds

        def recv(self, _size):
            if self._chunks:
                return self._chunks.pop(0)
            if self._then == "closed":
                return b""
            raise TimeoutError("still open, nothing more sent")

    def test_a_patch_6_request_ends_at_its_end_line_with_the_connection_open(self):
        conn = self.Connection([b"Intent { cmp=a/b }\nEXTRA:x=1\nBRIDGE_RE", b"PLY:1\nBRIDGE_END:1\n"], then="open")
        with patch.object(launch_bridge, "debug_log") as log:
            request = launch_bridge.read_request(conn)
        self.assertEqual(request, "Intent { cmp=a/b }\nEXTRA:x=1\nBRIDGE_REPLY:1")
        self.assertIn("BRIDGE_REPLY:1", request.splitlines())
        log.assert_not_called()

    def test_an_older_patch_ends_by_closing_its_side(self):
        conn = self.Connection([b"Intent { cmp=a/b }\nBRIDGE_REPLY:1"])
        self.assertEqual(launch_bridge.read_request(conn), "Intent { cmp=a/b }\nBRIDGE_REPLY:1")

    def test_a_stuck_sender_is_given_up_on_and_what_came_is_used(self):
        conn = self.Connection([b"Intent { cmp=a/b }"], then="open")
        with patch.object(launch_bridge, "debug_log"):
            self.assertEqual(launch_bridge.read_request(conn), "Intent { cmp=a/b }")
        self.assertEqual(conn.timeout, launch_bridge.REQUEST_TIMEOUT_SECONDS)


class BridgeRouteTests(unittest.TestCase):
    def _route(self, config, returncode=0, stderr=""):
        completed = subprocess.CompletedProcess(["adb"], returncode, "", stderr)
        with patch.object(launch_bridge.adb_client, "target_serial", return_value="emulator-5554"), \
             patch.object(launch_bridge.subprocess, "run", return_value=completed) as run, \
             patch("builtins.print") as printed:
            result = launch_bridge.route_bridge_port(config)
        return result, run.call_args.args[0], printed.call_args.args[0]

    def test_androids_fixed_port_is_pointed_at_the_bridges_real_port(self):
        ok, command, message = self._route({"bridge_port": 8123})
        self.assertTrue(ok)
        self.assertEqual(command, ["adb", "-s", "emulator-5554", "reverse", "tcp:7737", "tcp:8123"])
        self.assertIn("routed to the bridge on port 8123", message)

    def test_the_standard_port_is_routed_to_itself(self):
        _ok, command, _message = self._route({})
        self.assertEqual(command[-2:], ["tcp:7737", "tcp:7737"])

    def test_a_failure_is_reported_and_never_raised(self):
        ok, _command, message = self._route({}, returncode=1, stderr="adb: error: more than one device\n")
        self.assertFalse(ok)
        self.assertIn("more than one device", message)
        with patch.object(launch_bridge.adb_client, "target_serial", return_value=None), \
             patch.object(launch_bridge.subprocess, "run", side_effect=OSError("no adb")), patch("builtins.print"):
            self.assertFalse(launch_bridge.route_bridge_port({}))


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
