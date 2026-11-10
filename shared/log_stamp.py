"""Puts the time of day in front of every line a background script prints.

The launch bridge and a hotkey-triggered Stop run with no window, writing to
bridge.log and stop.log. Their lines said what happened but not when, so "the
game took ages to open" or "iiSU came back late" couldn't be read off the
log: there was no telling whether two lines were a second or a minute apart.
"""

from __future__ import annotations

import sys
import threading
import time


class TimestampedStream:
    """Wraps a text stream, writing "HH:MM:SS.mmm " at the start of each line.

    Lines are written whole. print() hands over its text and its line end as
    two separate writes, and the bridge prints from several threads (the
    hotkeys, the controller, the launches), so two lines could end up run
    together on one line with an empty one after it. Each thread's text is
    held until its line is complete and then written in one piece."""

    def __init__(self, stream, clock=time.time):
        self._stream = stream
        self._clock = clock
        self._lock = threading.Lock()
        self._pending = threading.local()

    def _stamp(self) -> str:
        now = self._clock()
        return time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d} "

    def write(self, text: str) -> int:
        if not text:
            return 0
        *lines, rest = (getattr(self._pending, "text", "") + text).split("\n")
        self._pending.text = rest
        if lines:
            stamp = self._stamp()
            with self._lock:
                self._stream.write("".join(f"{stamp}{line}\n" for line in lines))
        return len(text)

    def flush(self) -> None:
        """Also writes out this thread's unfinished line, so nothing printed
        without a line end is lost when the process ends."""
        rest = getattr(self._pending, "text", "")
        with self._lock:
            if rest:
                self._pending.text = ""
                self._stream.write(f"{self._stamp()}{rest}\n")
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
