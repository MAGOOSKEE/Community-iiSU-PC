"""Unit tests for setup_wizard.session_log: Setup's output is also kept in a file, so a run that
dies partway (a closed window, a crash) can still be diagnosed afterwards."""

import io
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

import setup_wizard  # noqa: E402; path set up above


class SessionLogTests(unittest.TestCase):
    def test_printed_text_reaches_both_the_screen_and_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "setup.log"
            screen = io.StringIO()
            real, sys.stdout = sys.stdout, screen
            try:
                with setup_wizard.session_log(log):
                    print("[setup] step one")
            finally:
                sys.stdout = real
            self.assertIn("step one", screen.getvalue())
            self.assertIn("[setup] step one", log.read_text(encoding="utf-8"))

    def test_the_log_survives_an_exception_and_stdout_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "setup.log"
            real = sys.stdout
            with self.assertRaises(RuntimeError):
                with setup_wizard.session_log(log):
                    print("got this far")
                    raise RuntimeError("boom")
            self.assertIs(sys.stdout, real)
            self.assertIn("got this far", log.read_text(encoding="utf-8"))

    def test_text_is_on_disk_before_the_session_ends(self):
        """A hard kill never reaches the `with` block's exit, so writes must be flushed as they happen."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "setup.log"
            real, sys.stdout = sys.stdout, io.StringIO()
            try:
                with setup_wizard.session_log(log):
                    print("mid-run line")
                    on_disk = log.read_text(encoding="utf-8")
            finally:
                sys.stdout = real
            self.assertIn("mid-run line", on_disk)

    def test_runs_append_and_an_oversized_log_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "setup.log"
            real, sys.stdout = sys.stdout, io.StringIO()
            try:
                with setup_wizard.session_log(log):
                    print("first run")
                with setup_wizard.session_log(log):
                    print("second run")
                text = log.read_text(encoding="utf-8")
                self.assertIn("first run", text)
                self.assertIn("second run", text)
                log.write_text("x" * (setup_wizard.SETUP_LOG_MAX_BYTES + 1), encoding="utf-8")
                with setup_wizard.session_log(log):
                    print("fresh start")
            finally:
                sys.stdout = real
            self.assertNotIn("xxxx", log.read_text(encoding="utf-8"))

    def test_an_unwritable_log_does_not_stop_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            real, sys.stdout = sys.stdout, io.StringIO()
            try:
                with setup_wizard.session_log(Path(tmp)):  # a directory can't be opened as a file
                    print("still runs")
                shown = sys.stdout.getvalue()
            finally:
                sys.stdout = real
            self.assertIn("still runs", shown)


if __name__ == "__main__":
    unittest.main()
