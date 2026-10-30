"""
Unit tests for shared/adb_client.py against a fake adb server: a local socket
that speaks the same request and reply format as the real one. Covers the
device list, picking the VM (never a phone) for shell commands, ANDROID_SERIAL,
exit statuses, server errors falling back to the adb command line, a server
that isn't running, and the device change stream. No real adb or VM involved.
"""

import os
import socket
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import adb_client  # noqa: E402; path set up above


def _message(text: str) -> bytes:
    data = text.encode("utf-8")
    return b"%04x" % len(data) + data


class FakeAdbServer:
    """Answers host:devices, host:transport:<serial>, host:transport-any, a
    following shell: request, and host:track-devices, recording requests."""

    def __init__(self, devices="emulator-5554\tdevice\n", shell_output="", track=(), fail_transport=False):
        self.devices = devices
        self.shell_output = shell_output
        self.track = list(track)
        self.fail_transport = fail_transport
        self.requests = []
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(5)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self):
        self._server.close()

    def _read_request(self, conn):
        length = int(self._read(conn, 4), 16)
        request = self._read(conn, length).decode("utf-8")
        self.requests.append(request)
        return request

    @staticmethod
    def _read(conn, count):
        data = b""
        while len(data) < count:
            chunk = conn.recv(count - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    def _serve(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            try:
                request = self._read_request(conn)
                if request == "host:devices":
                    conn.sendall(b"OKAY" + _message(self.devices))
                elif request == "host:track-devices":
                    conn.sendall(b"OKAY" + b"".join(_message(listing) for listing in self.track))
                elif request.startswith("host:transport"):
                    if self.fail_transport:
                        conn.sendall(b"FAIL" + _message("device not found"))
                        return
                    conn.sendall(b"OKAY")
                    command = self._read_request(conn)
                    conn.sendall(b"OKAY")
                    if command.startswith("shell:"):
                        conn.sendall(self.shell_output.encode("utf-8"))
                else:
                    conn.sendall(b"FAIL" + _message(f"unknown request {request}"))
            except ConnectionError:
                pass


class AdbClientTests(unittest.TestCase):
    def _server(self, **kwargs):
        server = FakeAdbServer(**kwargs)
        self.addCleanup(server.close)
        patcher = patch.object(adb_client, "ADB_PORT", server.port)
        patcher.start()
        self.addCleanup(patcher.stop)
        no_cli = patch.object(adb_client, "_run_adb", side_effect=AssertionError("the adb command line was used"))
        no_cli.start()
        self.addCleanup(no_cli.stop)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ANDROID_SERIAL", None)
        return server

    def test_device_list_over_the_socket(self):
        self._server(devices="emulator-5554\tdevice\n")
        self.assertEqual(adb_client.devices_text(), "emulator-5554\tdevice\n")

    def test_shell_goes_to_the_vm_not_a_phone(self):
        server = self._server(
            devices="R5CT1234ABC\tdevice\nemulator-5554\tdevice\n",
            shell_output="hello\n__iisupc_exit=0\n",
        )
        self.assertEqual(adb_client.shell("echo hello"), (0, "hello"))
        self.assertIn("host:transport:emulator-5554", server.requests)
        shell_request = next(r for r in server.requests if r.startswith("shell:"))
        self.assertIn("( echo hello ); echo __iisupc_exit=$?", shell_request)

    def test_android_serial_wins(self):
        server = self._server(shell_output="__iisupc_exit=0\n")
        with patch.dict(os.environ, {"ANDROID_SERIAL": "192.168.240.112:5555"}):
            adb_client.shell("true")
        self.assertIn("host:transport:192.168.240.112:5555", server.requests)

    def test_exit_status_and_output_lines(self):
        self._server(shell_output="line one\r\nline two\n__iisupc_exit=3\n")
        self.assertEqual(adb_client.shell("false"), (3, "line one\nline two"))
        self.assertEqual(
            list(adb_client.shell_lines("x")), ["line one", "line two", "__iisupc_exit=3"],
        )

    def test_no_vm_uses_any_transport(self):
        server = self._server(devices="", shell_output="__iisupc_exit=0\n")
        adb_client.shell("true")
        self.assertIn("host:transport-any", server.requests)

    def test_track_devices_yields_each_change(self):
        self._server(track=["emulator-5554\toffline\n", "emulator-5554\tdevice\n", ""])
        stream = adb_client.track_devices()
        self.assertEqual(next(stream), "emulator-5554\toffline\n")
        self.assertEqual(next(stream), "emulator-5554\tdevice\n")
        self.assertEqual(next(stream), "")
        with self.assertRaises(adb_client.AdbError):
            next(stream)

    def test_exit_status_marker(self):
        self.assertEqual(adb_client.exit_status("__iisupc_exit=0"), 0)
        self.assertEqual(adb_client.exit_status("__iisupc_exit=127"), 127)
        self.assertIsNone(adb_client.exit_status("bootanim_done"))
        self.assertIsNone(adb_client.exit_status("__iisupc_exit=x"))


class FallbackTests(unittest.TestCase):
    def setUp(self):
        # A port nothing listens on: every connection is refused.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.closed_port = probe.getsockname()[1]
        probe.close()
        patcher = patch.object(adb_client, "ADB_PORT", self.closed_port)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_stopped_server_is_not_started_when_asked_not_to(self):
        with patch.object(adb_client, "_run_adb") as run:
            self.assertEqual(adb_client.devices_text(start=False), "")
        run.assert_not_called()

    def test_a_stopped_server_is_started_then_the_command_line_answers(self):
        cli = subprocess.CompletedProcess([], 0, stdout="List of devices attached\nemulator-5554\tdevice\n", stderr="")
        with patch.object(adb_client, "_run_adb", return_value=cli) as run:
            listing = adb_client.devices_text()
        self.assertIn("emulator-5554\tdevice", listing)
        self.assertEqual(run.call_args_list[0].args[0], ["start-server"])
        self.assertEqual(run.call_args_list[-1].args[0], ["devices"])

    def test_shell_falls_back_to_the_command_line(self):
        cli = subprocess.CompletedProcess([], 0, stdout="hi\n__iisupc_exit=0\n", stderr="")
        with patch.object(adb_client, "_run_adb", return_value=cli) as run, \
             patch.object(adb_client, "devices_text", return_value="emulator-5554\tdevice\n"):
            self.assertEqual(adb_client.shell("echo hi"), (0, "hi"))
        self.assertEqual(run.call_args.args[0][:3], ["-s", "emulator-5554", "shell"])

    def test_a_server_error_falls_back_to_the_command_line(self):
        server = FakeAdbServer(fail_transport=True)
        self.addCleanup(server.close)
        cli = subprocess.CompletedProcess([], 0, stdout="__iisupc_exit=0\n", stderr="")
        with patch.object(adb_client, "ADB_PORT", server.port), \
             patch.object(adb_client, "_run_adb", return_value=cli) as run:
            self.assertEqual(adb_client.shell("true"), (0, ""))
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
