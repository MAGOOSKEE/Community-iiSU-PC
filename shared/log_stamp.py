"""Puts the time of day in front of every line a background script prints.

The launch bridge and a hotkey-triggered Stop run with no window, writing to
bridge.log and stop.log. Their lines said what happened but not when, so "the
game took ages to open" or "iiSU came back late" couldn't be read off the
log: there was no telling whether two lines were a second or a minute apart.
"""

from __future__ import annotations

import sys
import time


class TimestampedStream:
    """Wraps a text stream, writing "HH:MM:SS " at the start of each line."""

    def __init__(self, stream, clock=time.time):
        self._stream = stream
        self._clock = clock
        self._at_line_start = True

    def write(self, text: str) -> int:
        if not text:
            return 0
        pieces = []
        for part in text.splitlines(keepends=True):
            if self._at_line_start:
                now = self._clock()
                pieces.append(time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d} ")
            pieces.append(part)
            self._at_line_start = part.endswith("\n")
        self._stream.write("".join(pieces))
        return len(text)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def stamp_stdout() -> None:
    """Timestamps this process's output when it goes to a log file. Left
    alone when there is no output at all (pythonw.exe) or it is a console
    someone is watching, where the time is noise."""
    stream = sys.stdout
    if stream is None or isinstance(stream, TimestampedStream):
        return
    try:
        if stream.isatty():
            return
    except (AttributeError, ValueError):
        pass
    sys.stdout = TimestampedStream(stream)
    if sys.stderr is not None and sys.stderr is not stream:
        try:
            if not sys.stderr.isatty():
                sys.stderr = TimestampedStream(sys.stderr)
        except (AttributeError, ValueError):
            pass
