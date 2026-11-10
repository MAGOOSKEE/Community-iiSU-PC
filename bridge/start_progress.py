"""What the loading screen shows while Community-iiSU-PC starts: a percentage,
the step it is on, and (when "Show details" is on) the start log.

The loading screen is its own process (bridge/ui/boot_overlay_app.py), so the
start sequence tells it what is happening through a small status file that
the screen re-reads several times a second. A file, not a pipe: the start
sequence goes on to launch the emulator and the bridge, and a pipe would be
inherited by them (see start_iisu_pc.py on why pipes and long-lived children
don't mix). The file is written whole and swapped in, so the screen never
reads half of it.

The percentage is a real estimate, not decoration. A start has three steps
(the Android VM, the ROM library, iiSU and the launch bridge), and how long
each took on this PC is remembered from the last starts, separately for a
quick resume, a cold boot and an already running VM, because they differ by a
minute. Each step owns a share of the bar equal to its share of the expected
time. While a step runs the screen moves the bar through that share at the
pace the step took last time, slowing to a crawl near the end of the share so
it never claims a step is finished before it is. On a first start there is
nothing remembered and typical times are used.

Everything here is plain data and file writes, so it is tested without a
screen or a VM.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

STATUS_PATH = Path(__file__).parent / ".start_status.json"
TIMINGS_PATH = Path(__file__).parent / ".start_timings.json"

KIND_RESUME = "resume"
KIND_COLD = "cold"
KIND_RUNNING = "running"

# (key, what the loading screen says) in the order a start goes through them.
STEPS = [
    ("vm", "Starting the Android VM"),
    ("library", "Checking your ROM library"),
    ("iisu", "Starting iiSU and the launch bridge"),
]
STEP_LABELS = dict(STEPS)

# Typical seconds per step, used until this PC has its own from a real start.
DEFAULT_SECONDS = {
    KIND_RESUME: {"vm": 8.0, "library": 2.0, "iisu": 6.0},
    KIND_COLD: {"vm": 30.0, "library": 4.0, "iisu": 30.0},
    KIND_RUNNING: {"vm": 0.5, "library": 2.0, "iisu": 5.0},
}
# The bar starts a little way in, so "preparing" is visibly not "stuck at 0".
PREPARING_PERCENT = 3.0
MINIMUM_STEP_SECONDS = 0.5
LOG_LINES_KEPT = 200


def load_timings(path: Path = TIMINGS_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def expected_seconds(kind: str, timings: dict) -> dict[str, float]:
    """Seconds each step is expected to take for this kind of start: this
    PC's remembered times where it has them, typical ones otherwise."""
    remembered = timings.get(kind) if isinstance(timings.get(kind), dict) else {}
    expected = {}
    for key, default in DEFAULT_SECONDS.get(kind, DEFAULT_SECONDS[KIND_COLD]).items():
        value = remembered.get(key)
        usable = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        expected[key] = max(MINIMUM_STEP_SECONDS, float(value) if usable else default)
    return expected


def step_shares(expected: dict[str, float]) -> dict[str, tuple[float, float]]:
    """key -> (percent the bar is at when the step starts, percent it covers),
    the steps sharing PREPARING_PERCENT..100 in proportion to their time."""
    total = sum(expected[key] for key, _label in STEPS)
    shares = {}
    position = PREPARING_PERCENT
    for key, _label in STEPS:
        span = (100.0 - PREPARING_PERCENT) * expected[key] / total
        shares[key] = (position, span)
        position += span
    return shares


def percent_at(base: float, span: float, expected: float, elapsed: float) -> float:
    """Where the bar is `elapsed` seconds into a step. It covers the first 90%
    of the step's share at the pace the step took last time, then approaches
    the end of the share ever more slowly without reaching it, so a step that
    runs long keeps visibly moving but is never shown as done. The loading
    screen runs this same function between status updates."""
    if span <= 0 or expected <= 0:
        return base
    ratio = max(0.0, elapsed) / expected
    if ratio <= 0.9:
        fraction = ratio
    else:
        fraction = 0.9 + 0.09 * (1 - 1 / (1 + (ratio - 0.9) * 2))
    return base + span * min(fraction, 0.99)


class StartProgress:
    """Written to by the start sequence, read by the loading screen."""

    def __init__(
        self, path: Path = STATUS_PATH, timings_path: Path = TIMINGS_PATH, details: bool = False,
        clock=time.time,
    ):
        self.path = path
        self.timings_path = timings_path
        self.details = details
        self._clock = clock
        self._kind: str | None = None
        self._expected: dict[str, float] = {}
        self._shares: dict[str, tuple[float, float]] = {}
        self._step: str | None = None
        self._step_started = clock()
        self._durations: dict[str, float] = {}
        self._log: list[str] = []
        self._state = {
            "label": "Getting ready", "base": 0.0, "span": PREPARING_PERCENT, "expected": 2.0,
            "started": clock(), "done": False,
        }
        self._write()

    # == Called by the start sequence ==

    def plan(self, kind: str) -> None:
        """Says which kind of start this is, once the start sequence knows."""
        self._kind = kind
        self._expected = expected_seconds(kind, load_timings(self.timings_path))
        self._shares = step_shares(self._expected)

    def step(self, key: str, label: str | None = None) -> None:
        """Begins a step, ending the one before it."""
        now = self._clock()
        self._end_step(now)
        if not self._shares:
            self.plan(KIND_COLD)
        self._step, self._step_started = key, now
        base, span = self._shares.get(key, (PREPARING_PERCENT, 0.0))
        self._state.update(
            label=label or STEP_LABELS.get(key, key), base=round(base, 2), span=round(span, 2),
            expected=round(self._expected.get(key, 1.0), 2), started=now,
        )
        self._write()

    def log(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        self._log.append(line)
        del self._log[:-LOG_LINES_KEPT]
        self._write()

    def done(self, success: bool = True) -> None:
        """Ends the last step. A start that worked fills the bar and has its
        step times remembered for the next start of the same kind."""
        now = self._clock()
        self._end_step(now)
        if success:
            self._state.update(label="Ready", base=100.0, span=0.0, expected=1.0, started=now, done=True)
            self._write()
            self._remember()

    def remove(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    # == Internals ==

    def _end_step(self, now: float) -> None:
        if self._step is not None:
            self._durations[self._step] = max(0.0, now - self._step_started)
            self._step = None

    def _remember(self) -> None:
        """Blends this start's step times into the remembered ones, half and
        half, so one unusually slow start doesn't skew the next bar."""
        if self._kind is None or not self._durations:
            return
        timings = load_timings(self.timings_path)
        previous = timings.get(self._kind) if isinstance(timings.get(self._kind), dict) else {}
        blended = dict(previous)
        for key, seconds in self._durations.items():
            old = previous.get(key)
            usable = isinstance(old, (int, float)) and not isinstance(old, bool) and old > 0
            blended[key] = round((seconds + old) / 2 if usable else seconds, 2)
        timings[self._kind] = blended
        _write_json(self.timings_path, timings)

    def _write(self) -> None:
        _write_json(self.path, {**self._state, "details": self.details, "log": self._log[-LOG_LINES_KEPT:]})


class NoProgress:
    """Stands in when there is no loading screen to tell."""

    path = None

    def plan(self, kind: str) -> None: ...
    def step(self, key: str, label: str | None = None) -> None: ...
    def log(self, line: str) -> None: ...
    def done(self, success: bool = True) -> None: ...
    def remove(self) -> None: ...


def _write_json(path: Path, data: dict) -> None:
    """Best effort: a loading screen that can't be updated is never a reason
    to fail a start."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def read_status(path: Path) -> dict | None:
    """The status as the loading screen reads it, None when there is none or
    it is mid-swap."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def status_percent(status: dict, now: float) -> float:
    """The percentage a status stands for at time `now`."""
    try:
        if status.get("done"):
            return 100.0
        return max(0.0, min(99.9, percent_at(
            float(status["base"]), float(status["span"]), float(status["expected"]), now - float(status["started"]),
        )))
    except (KeyError, TypeError, ValueError):
        return 0.0
