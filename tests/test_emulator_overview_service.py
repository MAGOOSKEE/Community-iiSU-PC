import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "bridge"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bridge.services import emulator_overview_service as svc  # noqa: E402
from shared.emulator_defaults import build_emulators_map  # noqa: E402


class BuildRowsTests(unittest.TestCase):
    def setUp(self):
        self.emulators = build_emulators_map()
        self.rows = svc.build_rows(self.emulators)
        self.by_console = {row.console: row for row in self.rows}

    def test_one_row_per_console(self):
        consoles = [row.console for row in self.rows if row.console]
        self.assertEqual(len(consoles), len(set(consoles)))
        for console in ("psx", "ps2", "gc", "wii", "switch", "3ds", "snes", "steam"):
            self.assertIn(console, self.by_console)

    def test_default_rows_name_the_emulator_not_the_package(self):
        self.assertEqual(self.by_console["ps2"].emulator, "PCSX2")
        self.assertEqual(self.by_console["ps2"].console_label, "Sony PlayStation 2")
        self.assertTrue(self.by_console["ps2"].is_default)
        self.assertEqual(self.by_console["3ds"].console_label, "Nintendo 3DS")

    def test_retroarch_consoles_cannot_be_changed_here(self):
        snes = self.by_console["snes"]
        self.assertEqual(snes.emulator, "RetroArch")
        self.assertTrue(snes.by_extension)
        self.assertFalse(snes.can_change)

    def test_steam_is_by_link(self):
        steam = self.by_console["steam"]
        self.assertTrue(steam.launched_by_link)
        self.assertEqual(steam.status, svc.STATUS_BY_LINK)
        self.assertFalse(steam.can_change)

    def test_shared_package_is_reported(self):
        self.assertIn("Nintendo Wii", self.by_console["gc"].shares_package_with)

    def test_changed_mapping_is_named_and_flagged(self):
        changed = svc.apply_choice(self.emulators, "xyz.aethersx2.android", ["Dolphin.exe"], ["-b"])
        row = next(r for r in svc.build_rows(changed) if r.console == "ps2")
        self.assertEqual(row.emulator, "Dolphin")
        self.assertFalse(row.is_default)

    def test_custom_package_gets_an_other_row(self):
        emulators = dict(self.emulators)
        emulators["com.example.custom"] = {"exe_names": ["myemu.exe"], "pre_args": []}
        row = next(r for r in svc.build_rows(emulators) if r.package == "com.example.custom")
        self.assertEqual(row.emulator, "myemu")
        self.assertTrue(row.console_label.startswith("Other"))

    def test_catalog_ids(self):
        self.assertEqual(self.by_console["ps2"].catalog_id, "pcsx2")
        self.assertIsNone(self.by_console["switch"].catalog_id)


class CheckAndLibraryTests(unittest.TestCase):
    def test_check_installed_uses_the_finder_once_per_program(self):
        rows = svc.build_rows(build_emulators_map())
        calls = []

        def finder(names, roots, cache):
            calls.append(tuple(names))
            return Path("C:/emus/pcsx2-qt.exe") if "pcsx2-qt.exe" in names else None

        svc.check_installed(rows, ["C:/emus"], finder=finder)
        self.assertEqual(len(calls), len(set(calls)))
        ps2 = next(r for r in rows if r.console == "ps2")
        self.assertEqual(ps2.status, svc.STATUS_FOUND)
        psx = next(r for r in rows if r.console == "psx")
        self.assertEqual(psx.status, svc.STATUS_MISSING)

    def test_library_consoles_counts_files_and_applies_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "3ds").mkdir()
            (root / "3ds" / "game.3ds").write_text("x")
            (root / "ps2").mkdir()
            (root / "ps2" / "a.iso").write_text("x")
            (root / "ps2" / "b.iso").write_text("x")
            (root / "empty").mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "x.txt").write_text("x")
            names = {"3ds": "n3ds", "ps2": "ps2", "empty": "psx"}
            counts = svc.library_consoles(tmp, names.get)
        self.assertEqual(counts, {"3ds": 1, "ps2": 2})

    def test_summary_and_needed_downloads(self):
        rows = svc.build_rows(build_emulators_map())
        svc.check_installed(rows, [], finder=lambda names, roots, cache: None)
        svc.mark_library(rows, {"ps2": 3, "gc": 1})
        self.assertEqual(sorted(svc.needed_downloads(rows)), ["dolphin", "pcsx2"])
        text = svc.summary(rows)
        self.assertIn("PS2", text)
        self.assertIn("PCSX2", text)
        ordered = svc.sort_for_display(rows)
        self.assertTrue(ordered[0].in_library and ordered[1].in_library)

    def test_summary_without_library(self):
        rows = svc.build_rows(build_emulators_map())
        svc.check_installed(rows, [], finder=lambda names, roots, cache: None)
        self.assertIn("consoles have their emulator installed", svc.summary(rows))


class ApplyChoiceTests(unittest.TestCase):
    def test_drops_old_settings_and_keeps_rom_order_flag(self):
        emulators = {"pkg": {"exe_names": ["a.exe"], "pre_args": ["-x"], "rom_before_args": True}}
        updated = svc.apply_choice(emulators, "pkg", ["b.exe"], ["-y"])
        self.assertEqual(updated["pkg"], {"exe_names": ["b.exe"], "pre_args": ["-y"]})
        self.assertEqual(emulators["pkg"]["exe_names"], ["a.exe"])
        updated = svc.apply_choice(emulators, "pkg", ["c.exe"], [], rom_before_args=True)
        self.assertTrue(updated["pkg"]["rom_before_args"])

    def test_choices_exclude_retroarch_and_steam(self):
        labels = [c["label"] for c in svc.emulator_choices()]
        self.assertIn("PCSX2", labels)
        self.assertNotIn("Steam", labels)
        self.assertNotIn("RetroArch", labels)


if __name__ == "__main__":
    unittest.main()
