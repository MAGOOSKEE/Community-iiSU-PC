"""bridge/library_rescan.py: when iiSU is asked to rescan, and the marker
the ROM sync leaves when the library changed. No device: the adb shell is a
fake that answers like the patched iiSU's MediaBridge."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bridge"))

import library_rescan  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def fake_shell(ready_after: float, started_after: float, clock: FakeClock, calls: list):
    def shell(command, timeout=None):
        calls.append(command)
        if library_rescan.PING_ACTION in command:
            return 0, library_rescan.READY_MARKER if clock.now >= ready_after else "Broadcast completed: result=0"
        if clock.now >= started_after:
            return 0, "result=1 " + library_rescan.STARTED_MARKER
        return 0, "result=1 IISUPC_MEDIABRIDGE_RESCAN_FAILED_V1:NO_ACTIVITY"
    return shell


class RescanWhenReadyTests(unittest.TestCase):
    def run_case(self, ready_after, started_after, timeout=60.0):
        clock, calls = FakeClock(), []
        ok = library_rescan.rescan_when_ready(
            fake_shell(ready_after, started_after, clock, calls), timeout=timeout,
            sleep=clock.sleep, clock=clock, log=lambda line: None,
        )
        return ok, calls

    def test_waits_for_mediabridge_then_retries_until_started(self):
        ok, calls = self.run_case(ready_after=6, started_after=15)
        self.assertTrue(ok)
        self.assertTrue(any(library_rescan.RESCAN_ACTION in c for c in calls))
        self.assertGreater(sum(library_rescan.RESCAN_ACTION in c for c in calls), 1)

    def test_gives_up_when_mediabridge_never_answers(self):
        ok, calls = self.run_case(ready_after=10**6, started_after=0, timeout=20)
        self.assertFalse(ok)
        self.assertFalse(any(library_rescan.RESCAN_ACTION in c for c in calls))

    def test_gives_up_when_the_rescan_never_starts(self):
        ok, _calls = self.run_case(ready_after=0, started_after=10**6, timeout=30)
        self.assertFalse(ok)

    def test_shell_errors_are_retried(self):
        clock, attempts = FakeClock(), []

        def shell(command, timeout=None):
            attempts.append(command)
            if len(attempts) == 1:
                raise OSError("adb went away")
            return 0, library_rescan.READY_MARKER + library_rescan.STARTED_MARKER

        ok = library_rescan.rescan_when_ready(shell, sleep=clock.sleep, clock=clock, log=lambda line: None)
        self.assertTrue(ok)


class MarkerTests(unittest.TestCase):
    def test_mark_pending_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / ".library_changed"
            self.assertFalse(library_rescan.pending(marker))
            library_rescan.mark_changed(marker)
            self.assertTrue(library_rescan.pending(marker))
            library_rescan.clear(marker)
            self.assertFalse(library_rescan.pending(marker))


if __name__ == "__main__":
    unittest.main()
