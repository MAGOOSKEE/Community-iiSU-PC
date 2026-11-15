"""Tells iiSU to rescan its library after the ROM sync changed it, so new
games show up without anyone opening iiSU's settings to press "Rescan full
library" themselves.

How it fits together:
  1. sync_library.py mirrors the ROM folder into Android on every start.
     When the mirror actually changed, it leaves a marker file here.
  2. Once iiSU is open, launch_bridge.py (or the Manager's Sync Library
     Now) calls rescan_when_ready(), which asks the patched iiSU to run its
     own full rescan through MediaBridge's RESCAN_LIBRARY broadcast
     (installer/smali_patch/MediaBridgeReceiver.smali), then clears the
     marker. A rescan that couldn't run leaves the marker, so the next start
     tries again.

The broadcast only works once iiSU's main screen exists, which is a few
seconds after `am start` returns, so it is retried until iiSU says it
started or the time runs out. Kept free of the bridge's own state, with the
adb call passed in, so the timing logic is testable without a device.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

MARKER_PATH = Path(__file__).resolve().parent / ".library_changed"

COMPONENT = "com.iisulauncher/com.iisulauncher.pcbridge.MediaBridgeReceiver"
PING_ACTION = "com.iisulauncher.pcbridge.PING"
RESCAN_ACTION = "com.iisulauncher.pcbridge.RESCAN_LIBRARY"
READY_MARKER = "IISUPC_MEDIABRIDGE_READY_V1"
STARTED_MARKER = "IISUPC_MEDIABRIDGE_RESCAN_STARTED_V1"

# Shell is adb_client.shell's shape: (command, timeout=...) -> (status, output).
Shell = Callable[..., "tuple[int | None, str]"]


def mark_changed(path: Path | None = None) -> None:
    try:
        (path or MARKER_PATH).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def pending(path: Path | None = None) -> bool:
    return (path or MARKER_PATH).is_file()


def clear(path: Path | None = None) -> None:
    try:
        (path or MARKER_PATH).unlink()
    except OSError:
        pass


def broadcast(action: str) -> str:
    return f"am broadcast -a {action} -n {COMPONENT}"


def rescan_when_ready(
    shell: Shell,
    timeout: float = 120.0,
    settle: float = 5.0,
    retry_every: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> bool:
    """Waits for iiSU's MediaBridge to answer, gives iiSU a moment to put up
    its main screen, then asks for a full rescan until iiSU says it started.
    True when it did. An iiSU patched before MediaBridge existed never
    answers, and this gives up quietly after timeout."""
    deadline = clock() + timeout
    while True:
        _status, output = _safe(shell, broadcast(PING_ACTION))
        if READY_MARKER in output:
            break
        if clock() >= deadline:
            log("[rescan] iiSU's MediaBridge never answered, so its library wasn't rescanned.")
            return False
        sleep(retry_every)
    sleep(settle)
    last = ""
    while True:
        _status, output = _safe(shell, broadcast(RESCAN_ACTION))
        if STARTED_MARKER in output:
            log("[rescan] iiSU is rescanning its library.")
            return True
        last = output.strip().splitlines()[-1] if output.strip() else "no answer"
        if clock() >= deadline:
            log(f"[rescan] iiSU didn't start a rescan ({last}).")
            return False
        sleep(retry_every)


def _safe(shell: Shell, command: str) -> tuple[int | None, str]:
    try:
        return shell(command, timeout=20)
    except Exception as exc:  # noqa: BLE001; adb going away mid-wait is retried like any other miss
        return None, str(exc)
