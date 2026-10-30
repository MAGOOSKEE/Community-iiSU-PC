"""Elapsed-time bookkeeping for the start, stop and bridge logs: how long the
whole run took and how long each stage of it took, so a slow start can be
traced to the stage that was slow instead of guessed at."""

import time


class StageTimer:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._start = self._last = clock()
        self.stages: list[tuple[str, float]] = []

    @property
    def elapsed(self) -> float:
        """Seconds since the timer was created."""
        return self._clock() - self._start

    def mark(self, name: str) -> float:
        """Ends the stage called name (it began at the previous mark, or when
        the timer was created) and returns how many seconds it took."""
        now = self._clock()
        length = now - self._last
        self.stages.append((name, length))
        self._last = now
        return length

    def breakdown(self) -> str:
        """The stages so far, for one log line: "Android VM 34.1s, ROM library 0.4s"."""
        return ", ".join(f"{name} {length:.1f}s" for name, length in self.stages)
