"""The loading screen's percentage, step and log (bridge/start_progress.py),
and the start log that feeds it (start_iisu_pc.StartLog)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import start_iisu_pc  # noqa: E402
import start_progress as sp  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.status = self.root / "status.json"
        self.timings = self.root / "timings.json"
        self.clock = Clock()

    def _progress(self, **kwargs) -> sp.StartProgress:
        return sp.StartProgress(self.status, self.timings, clock=self.clock, **kwargs)

    def _percent(self) -> float:
        return sp.status_percent(sp.read_status(self.status), self.clock.now)


class SharesTests(unittest.TestCase):
    def test_steps_share_the_bar_by_how_long_they_take(self):
        shares = sp.step_shares({"vm": 30.0, "library": 10.0, "iisu": 60.0})
        self.assertAlmostEqual(shares["vm"][0], sp.PREPARING_PERCENT)
        self.assertAlmostEqual(shares["iisu"][1], 2 * shares["vm"][1])
        self.assertAlmostEqual(shares["iisu"][0] + shares["iisu"][1], 100.0)

    def test_remembered_times_replace_typical_ones(self):
        expected = sp.expected_seconds(sp.KIND_COLD, {"cold": {"vm": 80.0, "iisu": "soon", "library": -1}})
        self.assertEqual(expected["vm"], 80.0)
        self.assertEqual(expected["iisu"], sp.DEFAULT_SECONDS[sp.KIND_COLD]["iisu"])
        self.assertEqual(expected["library"], sp.DEFAULT_SECONDS[sp.KIND_COLD]["library"])

    def test_an_unknown_kind_of_start_still_has_a_plan(self):
        self.assertEqual(set(sp.expected_seconds("something new", {})), {"vm", "library", "iisu"})


class PercentAtTests(unittest.TestCase):
    def test_moves_at_the_pace_the_step_took_last_time(self):
        self.assertAlmostEqual(sp.percent_at(10.0, 40.0, 20.0, 10.0), 30.0)

    def test_never_reaches_the_end_of_its_share_however_long_it_runs(self):
        values = [sp.percent_at(10.0, 40.0, 20.0, seconds) for seconds in (18, 20, 40, 400, 40000)]
        self.assertEqual(values, sorted(values))
        self.assertLess(values[-1], 50.0)
        self.assertGreater(values[-1], values[0])

    def test_never_goes_backwards(self):
        previous = -1.0
        for tenth in range(0, 600):
            value = sp.percent_at(0.0, 100.0, 20.0, tenth / 10)
            self.assertGreaterEqual(value, previous)
            previous = value


class StartProgressTests(TempDirTestCase):
    def test_a_start_goes_from_a_few_percent_to_a_hundred(self):
        progress = self._progress()
        self.assertLess(self._percent(), sp.PREPARING_PERCENT + 0.01)
        progress.plan(sp.KIND_RESUME)
        seen = [self._percent()]
        for key in ("vm", "library", "iisu"):
            progress.step(key)
            seen.append(self._percent())
            self.clock.now += 3
            seen.append(self._percent())
        progress.done()
        seen.append(self._percent())
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen[-1], 100.0)
        self.assertLess(seen[-2], 100.0)

    def test_the_step_being_worked_on_is_named(self):
        progress = self._progress()
        progress.plan(sp.KIND_COLD)
        progress.step("library")
        self.assertEqual(sp.read_status(self.status)["label"], "Checking your ROM library")
        progress.step("vm", "Starting Waydroid")
        self.assertEqual(sp.read_status(self.status)["label"], "Starting Waydroid")

    def test_a_step_before_any_plan_still_works(self):
        progress = self._progress()
        progress.step("vm")
        self.assertGreater(sp.read_status(self.status)["span"], 0)

    def test_log_lines_and_the_details_choice_reach_the_screen(self):
        progress = self._progress(details=True)
        progress.log("[start] one\n")
        progress.log("   ")
        progress.log("[start] two")
        status = sp.read_status(self.status)
        self.assertTrue(status["details"])
        self.assertEqual(status["log"], ["[start] one", "[start] two"])

    def test_only_the_latest_log_lines_are_kept(self):
        progress = self._progress()
        for number in range(sp.LOG_LINES_KEPT + 50):
            progress.log(f"line {number}")
        log = sp.read_status(self.status)["log"]
        self.assertEqual(len(log), sp.LOG_LINES_KEPT)
        self.assertEqual(log[-1], f"line {sp.LOG_LINES_KEPT + 49}")

    def test_a_finished_start_is_remembered_for_the_next_one_of_its_kind(self):
        progress = self._progress()
        progress.plan(sp.KIND_COLD)
        for key, seconds in (("vm", 50), ("library", 2), ("iisu", 20)):
            progress.step(key)
            self.clock.now += seconds
        progress.done()
        remembered = json.loads(self.timings.read_text(encoding="utf-8"))
        self.assertEqual(remembered, {"cold": {"vm": 50.0, "library": 2.0, "iisu": 20.0}})
        self.assertEqual(sp.expected_seconds(sp.KIND_COLD, remembered)["vm"], 50.0)
        # A quick resume has its own times.
        self.assertEqual(sp.expected_seconds(sp.KIND_RESUME, remembered)["vm"], sp.DEFAULT_SECONDS[sp.KIND_RESUME]["vm"])

    def test_one_slow_start_only_moves_the_remembered_time_halfway(self):
        self.timings.write_text(json.dumps({"cold": {"vm": 20.0}}), encoding="utf-8")
        progress = self._progress()
        progress.plan(sp.KIND_COLD)
        progress.step("vm")
        self.clock.now += 100
        progress.done()
        self.assertEqual(json.loads(self.timings.read_text(encoding="utf-8"))["cold"]["vm"], 60.0)

    def test_a_failed_start_is_not_remembered_and_never_shows_a_hundred(self):
        progress = self._progress()
        progress.plan(sp.KIND_COLD)
        progress.step("vm")
        self.clock.now += 300
        progress.done(success=False)
        self.assertFalse(self.timings.exists())
        self.assertLess(self._percent(), 100.0)

    def test_remove_takes_the_status_file_away(self):
        progress = self._progress()
        progress.remove()
        self.assertFalse(self.status.exists())
        self.assertIsNone(sp.read_status(self.status))

    def test_a_status_file_that_cannot_be_written_never_raises(self):
        progress = sp.StartProgress(self.root / "no-such-folder" / "status.json", self.timings, clock=self.clock)
        progress.step("vm")
        progress.log("line")
        progress.done()

    def test_no_progress_accepts_everything(self):
        nothing = sp.NoProgress()
        nothing.plan(sp.KIND_COLD)
        nothing.step("vm")
        nothing.log("line")
        nothing.done()
        nothing.remove()
        self.assertIsNone(nothing.path)

    def test_a_damaged_status_reads_as_zero_not_an_error(self):
        self.assertEqual(sp.status_percent({"base": "x"}, 0.0), 0.0)


class StartLogTests(TempDirTestCase):
    def test_lines_reach_the_file_with_a_time_and_the_screen_without(self):
        progress = self._progress()
        log_path = self.root / "start.log"
        with patch("sys.stdout", None):
            with start_iisu_pc.StartLog(progress, log_path):
                print("[start] first")
                print("[start] sec", end="")
                print("ond")
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertRegex(lines[0], r"^\d\d:\d\d:\d\d \[start\] first$")
        self.assertTrue(lines[1].endswith("[start] second"))
        self.assertEqual(sp.read_status(self.status)["log"], ["[start] first", "[start] second"])

    def test_what_was_there_before_still_gets_the_output_and_comes_back(self):
        class Sink:
            def __init__(self):
                self.text = ""

            def write(self, text):
                self.text += text

            def flush(self):
                pass

        sink = Sink()
        with patch("sys.stdout", sink):
            with start_iisu_pc.StartLog(None, self.root / "start.log"):
                print("hello")
            self.assertIs(sys.stdout, sink)
        self.assertEqual(sink.text, "hello\n")

    def test_new_bridge_log_lines_are_forwarded_once_and_whole(self):
        progress = self._progress()
        bridge_log = self.root / "bridge.log"
        bridge_log.write_bytes(b"[bridge] one\n[bridge] tw")
        offset = start_iisu_pc.forward_new_lines(bridge_log, 0, progress)
        self.assertEqual(sp.read_status(self.status)["log"], ["[bridge] one"])
        bridge_log.write_bytes(b"[bridge] one\n[bridge] two\n")
        offset = start_iisu_pc.forward_new_lines(bridge_log, offset, progress)
        self.assertEqual(sp.read_status(self.status)["log"], ["[bridge] one", "[bridge] two"])
        self.assertEqual(start_iisu_pc.forward_new_lines(bridge_log, offset, progress), offset)

    def test_a_missing_log_is_not_an_error(self):
        self.assertEqual(start_iisu_pc.forward_new_lines(self.root / "nope.log", 7, self._progress()), 7)


if __name__ == "__main__":
    unittest.main()
