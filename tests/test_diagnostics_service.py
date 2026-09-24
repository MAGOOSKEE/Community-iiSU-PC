"""Smoke tests for bridge/services/diagnostics_service.py -- most of what
it does is genuinely environment-dependent (is ADB present, is the bridge
listening, is Steam installed), so these mainly confirm it runs cleanly
and returns well-formed results rather than asserting exact findings."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import diagnostics_service as svc


class RunDiagnosticsTests(unittest.TestCase):
    def test_returns_well_formed_rows(self):
        results = svc.run_diagnostics({})
        self.assertGreater(len(results), 0)
        for status, check, details in results:
            self.assertIn(status, {"OK", "WARNING", "ERROR"})
            self.assertIsInstance(check, str)
            self.assertIsInstance(details, str)

    def test_summarize_counts_each_status(self):
        results = [("OK", "a", ""), ("OK", "b", ""), ("WARNING", "c", ""), ("ERROR", "d", "")]
        summary = svc.summarize(results)
        self.assertIn("2 OK", summary)
        self.assertIn("1 Warning", summary)
        self.assertIn("1 Error", summary)

    def test_summarize_pluralizes_correctly(self):
        summary = svc.summarize([("WARNING", "a", ""), ("WARNING", "b", "")])
        self.assertIn("2 Warnings", summary)
        summary_one = svc.summarize([("ERROR", "a", "")])
        self.assertIn("1 Error", summary_one)
        self.assertNotIn("1 Errors", summary_one)


if __name__ == "__main__":
    unittest.main()
