"""A small client for the adb server's own socket protocol.

Every `adb ...` command line starts a new adb.exe process that connects to the
adb server on 127.0.0.1:5037 and forwards one request. Doing that directly costs
well under a millisecond instead of about 26 ms per call (measured on Windows),
which matters for the checks that run every couple of seconds for a whole
session and for the boot waits that poll in a loop. The server itself is still
the real adb server; this only replaces the command line client.

Protocol (adb's SERVICES.TXT): a request is its length as 4 hex digits followed
by the text; the server answers OKAY, or FAIL plus a length prefixed message.
"host:..." requests go to the server, everything else to a device after
"host:transport:<serial>" picks one.

Every function here falls back to the adb command line when the socket path
fails for any reason, so a broken or missing server never makes things worse
than before.
"""

import os
import shlex
import socket
import subprocess

from shared.platform_compat import subprocess_creationflags
from shared.vm_backend import vm_device_serial

ADB_HOST = "127.0.0.1"
ADB_PORT = int(os.environ.get("ANDROID_ADB_SERVER_PORT", "5037"))
# A running server accepts a local connection at once. With no server, Windows
# doesn't refuse straight away but retries for about 2 seconds, so no answer
# within this long counts as no server.
CONNECT_TIMEOUT = 0.5
_EXIT_MARKER = "__iisupc_exit="


class AdbError(RuntimeError):
    pass


def _send(sock: socket.socket, request: str) -> None:
    data = request.encode("utf-8")
    sock.sendall(b"%04x" % len(data) + data)


def _read_exact(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise AdbError("the adb server closed the connection")
        data += chunk
    return data


def _read_message(sock: socket.socket) -> bytes:
    return _read_exact(sock, int(_read_exact(sock, 4), 16))


def _read_status(sock: socket.socket) -> None:
    status = _read_exact(sock, 4)
    if status == b"OKAY":
        return
    if status == b"FAIL":
        raise AdbError(_read_message(sock).decode("utf-8", errors="replace"))
    raise AdbError(f"unexpected reply from the adb server: {status!r}")


def _connect(timeout: float | None) -> socket.socket:
    """Raises OSError when no server answers within CONNECT_TIMEOUT."""
    sock = socket.create_connection((ADB_HOST, ADB_PORT), timeout=CONNECT_TIMEOUT)
    sock.settimeout(timeout)
    return sock


def _run_adb(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", *args], capture_output=True, text=True, timeout=timeout,
        creationflags=subprocess_creationflags(),
    )


def start_server() -> None:
    """Starts the adb server if it isn't running, the one thing that still needs
    the command line. Any `adb` command would start it implicitly before."""
    try:
        _run_adb(["start-server"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass


def devices_text(timeout: float = 3.0, start: bool = True) -> str:
    """The device list as "<serial>\\t<state>" lines, the same lines `adb devices`
    prints below its header, so shared.vm_backend's parsers read either. With
    start=False a server that isn't running counts as no devices, instead of
    being started."""
    for attempt in (1, 2):
        try:
            sock = _connect(timeout)
        except OSError:
            if not start:
                return ""
            if attempt == 1:
                start_server()
                continue
            break
        try:
            with sock:
                _send(sock, "host:devices")
                _read_status(sock)
                return _read_message(sock).decode("utf-8", errors="replace")
        except (OSError, AdbError):
            break
    try:
        return _run_adb(["devices"], timeout=timeout + 5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _pick_serial(serial: str | None) -> str | None:
    """The device a shell command goes to: the given serial, else ANDROID_SERIAL
    (how Waydroid runs are pinned), else the Android VM, never a phone on USB."""
    return serial or os.environ.get("ANDROID_SERIAL") or vm_device_serial(devices_text())


def shell_lines(command: str, serial: str | None = None, timeout: float = 30.0):
    """Runs a shell command on the device and yields its output lines (stdout
    and stderr together) as they arrive, so a caller can time each one. The
    last line is the exit status marker; exit_status() reads it. Over the
    command line fallback the lines only arrive once the command has ended."""
    target = _pick_serial(serial)
    wrapped = f"( {command} ); echo {_EXIT_MARKER}$?"
    try:
        sock = _connect(timeout)
    except OSError:
        sock = None
    if sock is not None:
        started_output = False
        try:
            with sock:
                _send(sock, f"host:transport:{target}" if target else "host:transport-any")
                _read_status(sock)
                _send(sock, f"shell:{wrapped}")
                _read_status(sock)
                pending = b""
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    started_output = True
                    pending += chunk
                    *complete, pending = pending.split(b"\n")
                    for line in complete:
                        yield line.decode("utf-8", errors="replace").rstrip("\r")
                if pending:
                    yield pending.decode("utf-8", errors="replace").rstrip("\r")
                return
        except (OSError, AdbError):
            if started_output:
                # Part of the output is already out, rerunning would repeat it.
                # Ending here means no exit status, which callers treat as failure.
                return
    args = (["-s", target] if target else []) + ["shell", wrapped]
    try:
        result = _run_adb(args, timeout=timeout + 5)
    except (OSError, subprocess.SubprocessError):
        return
    yield from (result.stdout + result.stderr).splitlines()


def exit_status(line: str) -> int | None:
    """The exit status a shell_lines() marker line carries, or None for any
    other line."""
    if not line.startswith(_EXIT_MARKER):
        return None
    try:
        return int(line[len(_EXIT_MARKER):].strip())
    except ValueError:
        return None


def shell(command: str, serial: str | None = None, timeout: float = 30.0) -> tuple[int | None, str]:
    """Runs a shell command on the device and returns (exit status, output with
    stdout and stderr together). The status is None when it couldn't be read."""
    status = None
    lines = []
    for line in shell_lines(command, serial, timeout):
        code = exit_status(line)
        if code is None:
            lines.append(line)
        else:
            status = code
    return status, "\n".join(lines)


def quote(value: str) -> str:
    """Quotes one argument for the device's shell."""
    return shlex.quote(value)


def track_devices(timeout: float | None = None):
    """Yields the device list (as devices_text() formats it) every time it
    changes, starting with the current one, until the server goes away. One
    connection for the whole session instead of a poll every few seconds.
    Raises OSError or AdbError when the server can't be reached."""
    with _connect(timeout) as sock:
        _send(sock, "host:track-devices")
        _read_status(sock)
        while True:
            yield _read_message(sock).decode("utf-8", errors="replace")
