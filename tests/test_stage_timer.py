"""Unit tests for shared/stage_timer.py, driven by a fake clock."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.stage_timer import StageTimer  # noqa: E402; path set up above


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class StageTimerTests(unittest.TestCase):
    def test_elapsed_counts_from_creation(self):
        clock = FakeClock()
        timer = StageTimer(clock)
        clock.now += 12.5
        self.assertAlmostEqual(timer.elapsed, 12.5)

    def test_each_mark_measures_from_the_previous_one(self):
        clock = FakeClock()
        timer = StageTimer(clock)
        clock.now += 34.1
        self.assertAlmostEqual(timer.mark("Android VM"), 34.1)
        clock.now += 0.4
        self.assertAlmostEqual(timer.mark("ROM library"), 0.4)
        self.assertAlmostEqual(timer.elapsed, 34.5)
        self.assertEqual(timer.breakdown(), "Android VM 34.1s, ROM library 0.4s")

    def test_no_stages_yet(self):
        self.assertEqual(StageTimer(FakeClock()).breakdown(), "")


if __name__ == "__main__":
    unittest.main()
