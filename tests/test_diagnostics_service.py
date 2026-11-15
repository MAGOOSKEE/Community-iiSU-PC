"""Smoke tests for bridge/services/diagnostics_service.py, most of what
it does is genuinely environment-dependent (is ADB present, is the bridge
listening, is Steam installed), so these mainly confirm it runs cleanly
and returns well-formed results rather than asserting exact findings."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))
sys.path.insert(0, str(PROJECT_ROOT))

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


class TrimReleaseNotesTests(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertEqual(svc.trim_release_notes(None), "")
        self.assertEqual(svc.trim_release_notes("  \n "), "")

    def test_normalizes_windows_newlines_and_strips(self):
        self.assertEqual(svc.trim_release_notes("\r\n- one\r\n- two\r\n"), "- one\n- two")

    def test_short_notes_are_untouched(self):
        self.assertEqual(svc.trim_release_notes("a\nb", limit=10), "a\nb")

    def test_long_notes_are_cut_at_a_line_boundary_with_a_marker(self):
        notes = "\n".join(f"- line {i}" for i in range(100))
        trimmed = svc.trim_release_notes(notes, limit=60)
        self.assertTrue(trimmed.endswith("\n..."))
        self.assertLessEqual(len(trimmed), 64)

    def test_single_huge_line_is_hard_cut(self):
        self.assertEqual(svc.trim_release_notes("x" * 50, limit=10), "x" * 10 + "...")


class VmStorageCheckTests(unittest.TestCase):
    FULL = "Filesystem Size Used Avail Use% Mounted on\n/dev/block/dm-55 5.8G 5.6G 0 100% /data\n"
    LOW = "Filesystem Size Used Avail Use% Mounted on\n/dev/block/dm-55 16G 15G 900M 95% /data\n"
    FINE = "Filesystem Size Used Avail Use% Mounted on\n/dev/block/dm-55 16G 3G 13G 20% /data\n"

    def test_full_disk_is_an_error_that_names_the_crash_and_the_small_partition(self):
        status, name, details = svc.vm_storage_check(self.FULL, configured_gb=6.0)
        self.assertEqual((status, name), ("ERROR", "Android VM storage"))
        self.assertIn("database or disk is full", details)
        self.assertIn("6 GB", details)
        self.assertIn("Settings > Storage", details)

    def test_low_disk_is_a_warning(self):
        self.assertEqual(svc.vm_storage_check(self.LOW, configured_gb=16.0)[0], "WARNING")

    def test_healthy_disk_is_ok_and_needs_no_advice(self):
        status, _name, details = svc.vm_storage_check(self.FINE, configured_gb=16.0)
        self.assertEqual(status, "OK")
        self.assertNotIn("Settings > Storage", details)

    def test_no_partition_advice_when_it_is_already_big_or_unknown(self):
        self.assertNotIn("Settings > Storage", svc.vm_storage_check(self.FULL, configured_gb=16.0)[2])
        self.assertNotIn("Settings > Storage", svc.vm_storage_check(self.FULL, configured_gb=None)[2])

    def test_unreadable_output_is_a_warning_not_a_crash(self):
        self.assertEqual(svc.vm_storage_check("")[0], "WARNING")
        self.assertEqual(svc.vm_storage_check("df: /data: No such file")[0], "WARNING")


if __name__ == "__main__":
    unittest.main()


class ReportAndFixTests(unittest.TestCase):
    def test_redact_removes_home_and_user(self):
        text = svc.redact(r"C:\Users\Alice\ROMs and alice wrote this", home=r"C:\Users\Alice", user="alice")
        self.assertEqual(text, "~\ROMs and <user> wrote this")

    def test_redact_handles_forward_slashes(self):
        self.assertEqual(svc.redact("C:/Users/Alice/x", home=r"C:\Users\Alice", user=""), "~/x")

    def test_report_lists_problems_first(self):
        report = svc.report_text(
            [("OK", "a", "fine"), ("ERROR", "b", "broken"), ("WARNING", "c", "hmm")], home="/nowhere", user=""
        )
        lines = report.splitlines()
        self.assertTrue(lines[2].startswith("[ERROR] b"))
        self.assertTrue(lines[3].startswith("[WARNING] c"))

    def test_fixes_only_for_problems(self):
        self.assertIsNone(svc.fix_for("OK", "ROMs directory"))
        self.assertEqual(svc.fix_for("WARNING", "ROMs directory")[0], "roms")
        self.assertIsNone(svc.fix_for("ERROR", "Something unknown"))

    def test_emulators_check(self):
        import tempfile

        from shared.emulator_defaults import build_emulators_map

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ps2").mkdir()
            (Path(tmp) / "ps2" / "game.iso").write_text("x")
            config = {"roms_dir": tmp, "search_roots": [], "emulators": build_emulators_map()}
            status, check, details = svc.emulators_check(config, finder=lambda names, roots, cache: None)
            self.assertEqual(status, "WARNING")
            self.assertIn("PCSX2", details)
            status, _, _ = svc.emulators_check(config, finder=lambda names, roots, cache: Path("x.exe"))
            self.assertEqual(status, "OK")
