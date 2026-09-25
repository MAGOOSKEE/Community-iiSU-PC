"""
Unit tests for bridge/services/windows_apps_service.py, the Windows Apps
page's non-UI logic, extracted ahead of porting that page to Qt (see the
Qt rewrite plan: the two largest pages get their service layer extracted
and tested before their UI is written).
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import windows_apps_service as svc


class PcgameNameSafetyTests(unittest.TestCase):
    def test_rejects_reserved_dos_names(self):
        self.assertTrue(svc.windows_reserved_filename("CON"))
        self.assertTrue(svc.windows_reserved_filename("con.pcgame"))
        self.assertTrue(svc.windows_reserved_filename("COM1"))
        self.assertTrue(svc.windows_reserved_filename("LPT9.txt"))
        self.assertFalse(svc.windows_reserved_filename("Controller"))
        self.assertFalse(svc.windows_reserved_filename("COM10"))

    def test_safe_pcgame_name_rejects_bad_characters_and_reserved_names(self):
        self.assertIsNone(svc.safe_pcgame_name(""))
        self.assertIsNone(svc.safe_pcgame_name(".."))
        self.assertIsNone(svc.safe_pcgame_name("Game: Remastered"))
        self.assertIsNone(svc.safe_pcgame_name("CON"))
        self.assertEqual(svc.safe_pcgame_name("  My Game  "), "My Game")

    def test_safe_steam_pcgame_name_sanitizes_instead_of_rejecting(self):
        self.assertEqual(svc.safe_steam_pcgame_name("Half-Life: Alyx"), "Half-Life - Alyx")
        self.assertEqual(svc.safe_steam_pcgame_name("   "), "Steam Game")
        self.assertEqual(svc.safe_steam_pcgame_name("CON"), "CON - Game")

    def test_unique_windows_app_name_avoids_collisions(self):
        apps = {"Game": {}, "Game (2)": {}}
        self.assertEqual(svc.unique_windows_app_name("Other", apps), "Other")
        self.assertEqual(svc.unique_windows_app_name("Game", apps), "Game (3)")


class SteamUriTests(unittest.TestCase):
    def test_valid_uri(self):
        self.assertTrue(svc.valid_uri("steam://run/440"))
        self.assertFalse(svc.valid_uri("not a uri"))
        self.assertFalse(svc.valid_uri(""))

    def test_is_steam_uri(self):
        self.assertTrue(svc.is_steam_uri("steam://run/440"))
        self.assertTrue(svc.is_steam_uri("steam://rungameid/440?a=b"))
        self.assertFalse(svc.is_steam_uri("steam://install/440"))
        self.assertFalse(svc.is_steam_uri("https://store.steampowered.com/app/440"))

    def test_steam_app_id_accepts_id_uri_or_store_url(self):
        self.assertEqual(svc.steam_app_id("440"), "440")
        self.assertEqual(svc.steam_app_id("steam://run/440"), "440")
        self.assertEqual(svc.steam_app_id("steam://rungameid/440/extra"), "440")
        self.assertEqual(svc.steam_app_id("https://store.steampowered.com/app/440/TF2/"), "440")
        self.assertEqual(svc.steam_app_id("https://steamcommunity.com/app/440"), "440")
        self.assertIsNone(svc.steam_app_id("not steam at all"))

    def test_parse_steam_vdf_strings(self):
        text = '"appid"\t\t"440"\n"name"\t\t"Team Fortress 2"\n'
        self.assertEqual(svc.parse_steam_vdf_strings(text), {"appid": "440", "name": "Team Fortress 2"})

    def test_steam_ids_already_added(self):
        apps = {
            "TF2": {"type": "uri", "uri": "steam://run/440"},
            "MyApp": {"type": "executable", "exe": "C:/app.exe"},
            "Broken": "not a dict",
        }
        self.assertEqual(svc.steam_ids_already_added(apps), {"440"})


class WindowsAppStatusTests(unittest.TestCase):
    def test_invalid_entry(self):
        self.assertEqual(svc.windows_app_status("X", "not a dict"), "\u2717 Invalid entry")

    def test_executable_missing_exe_field(self):
        self.assertEqual(svc.windows_app_status("X", {"type": "executable", "exe": ""}), "\u2717 No EXE")

    def test_executable_missing_file(self):
        entry = {"type": "executable", "exe": "Z:/definitely/not/here.exe"}
        self.assertEqual(svc.windows_app_status("X", entry), "\u2717 Missing EXE")

    def test_uri_invalid(self):
        entry = {"type": "uri", "uri": "not a uri"}
        self.assertEqual(svc.windows_app_status("X", entry), "\u2717 Invalid URI")

    def test_steam_uri_not_installed(self):
        entry = {"type": "uri", "uri": "steam://run/440"}
        self.assertEqual(svc.windows_app_status("X", entry, installed_steam_ids_set=set()), "\u25cb Steam game not installed")

    def test_unknown_type(self):
        self.assertEqual(svc.windows_app_status("X", {"type": "bogus"}), "\u2717 Unknown type")

    def test_display_type(self):
        self.assertEqual(svc.windows_app_display_type({"type": "executable"}), "Executable")
        self.assertEqual(svc.windows_app_display_type({"type": "uri", "uri": "steam://run/440"}), "Steam Game")
        self.assertEqual(svc.windows_app_display_type({"type": "uri", "uri": "customscheme://x"}), "Custom URI")

    def test_sort_key_by_column(self):
        entry = {"added_at": "2026-01-01T00:00:00"}
        self.assertEqual(svc.windows_app_sort_key("name", "Zelda", entry, "Executable", "\u2713 Ready"), "zelda")
        self.assertEqual(svc.windows_app_sort_key("type", "Zelda", entry, "Executable", "\u2713 Ready"), "executable")
        self.assertEqual(svc.windows_app_sort_key("status", "Zelda", entry, "Executable", "\u2713 Ready"), "\u2713 ready")
        self.assertEqual(svc.windows_app_sort_key("added", "Zelda", entry, "Executable", "\u2713 Ready"), "2026-01-01T00:00:00")


class WindowsAppsJsonTests(unittest.TestCase):
    def test_load_missing_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(svc, "WINDOWS_APPS_PATH", Path(tmp) / "windows_apps.json"):
                self.assertEqual(svc.load_windows_apps(), {})

    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "windows_apps.json"
            with patch.object(svc, "WINDOWS_APPS_PATH", path):
                svc.save_windows_apps({"MyApp": {"type": "executable", "exe": "C:/app.exe"}})
                self.assertEqual(svc.load_windows_apps(), {"MyApp": {"type": "executable", "exe": "C:/app.exe"}})

    def test_load_corrupt_json_raises_service_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "windows_apps.json"
            path.write_text("{not valid json", encoding="utf-8")
            with patch.object(svc, "WINDOWS_APPS_PATH", path):
                with self.assertRaises(svc.WindowsAppsServiceError):
                    svc.load_windows_apps()

    def test_ensure_added_at_sets_timestamp_once(self):
        entry = svc.ensure_added_at({"type": "executable"})
        self.assertIn("added_at", entry)
        untouched = svc.ensure_added_at(entry)
        self.assertEqual(untouched["added_at"], entry["added_at"])


class LegacyStubMigrationTests(unittest.TestCase):
    def test_no_roms_dir_is_a_no_op(self):
        self.assertIsNone(svc.legacy_windows_stubs_dir(""))
        self.assertEqual(svc.migrate_legacy_windows_stubs(""), 0)

    def test_migrates_pcgame_files_additively(self):
        with tempfile.TemporaryDirectory() as tmp:
            roms_dir = Path(tmp) / "roms"
            legacy_dir = roms_dir / "windows"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "Game.pcgame").write_text("{}", encoding="utf-8")
            stubs_dir = Path(tmp) / "stubs"

            with patch.object(svc, "WINDOWS_STUBS_DIR", stubs_dir):
                copied = svc.migrate_legacy_windows_stubs(str(roms_dir))
                self.assertEqual(copied, 1)
                self.assertTrue((stubs_dir / "Game.pcgame").is_file())

                # Running again copies nothing new (existing target files are left alone).
                (stubs_dir / "Game.pcgame").write_text("edited", encoding="utf-8")
                copied_again = svc.migrate_legacy_windows_stubs(str(roms_dir))
                self.assertEqual(copied_again, 0)
                self.assertEqual((stubs_dir / "Game.pcgame").read_text(encoding="utf-8"), "edited")

    def test_delete_legacy_stubs_removes_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            roms_dir = Path(tmp) / "roms"
            legacy_dir = roms_dir / "windows"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "Game.pcgame").write_text("{}", encoding="utf-8")

            svc.delete_legacy_windows_stubs(str(roms_dir))
            self.assertFalse(legacy_dir.exists())

    def test_delete_legacy_stubs_without_a_folder_raises(self):
        with self.assertRaises(svc.WindowsAppsServiceError):
            svc.delete_legacy_windows_stubs("")


class CreateEditRepairTests(unittest.TestCase):
    def test_create_windows_app_rejects_case_insensitive_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp) / "stubs"
            apps = {"MyApp": {}}
            with self.assertRaises(svc.WindowsAppsServiceError):
                svc.create_windows_app("myapp", {"type": "executable", "exe": "C:/x.exe"}, apps, windows_dir)

    def test_create_windows_app_writes_placeholder_and_stamps_added_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp) / "stubs"
            apps: dict = {}
            svc.create_windows_app("MyApp", {"type": "executable", "exe": "C:/x.exe"}, apps, windows_dir)
            self.assertTrue((windows_dir / "MyApp.pcgame").is_file())
            self.assertIn("added_at", apps["MyApp"])

    def test_rename_placeholder_moves_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp)
            (windows_dir / "Old.pcgame").write_text("", encoding="utf-8")
            svc.rename_windows_app_placeholder(windows_dir, "Old", "New")
            self.assertFalse((windows_dir / "Old.pcgame").exists())
            self.assertTrue((windows_dir / "New.pcgame").exists())

    def test_repair_missing_placeholders_only_creates_whats_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp)
            (windows_dir / "Present.pcgame").write_text("", encoding="utf-8")
            repaired = svc.repair_missing_placeholders(windows_dir, ["Present", "Missing"])
            self.assertEqual(repaired, 1)
            self.assertTrue((windows_dir / "Missing.pcgame").is_file())

    def test_find_missing_and_orphan_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp)
            (windows_dir / "Orphan.pcgame").write_text("", encoding="utf-8")
            apps = {"NoPlaceholder": {}}
            missing, orphans = svc.find_missing_and_orphan_placeholders(apps, windows_dir)
            self.assertEqual(missing, ["NoPlaceholder"])
            self.assertEqual([p.name for p in orphans], ["Orphan.pcgame"])


class HealthScanTests(unittest.TestCase):
    def test_flags_missing_executable_and_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp)
            apps = {"Broken": {"type": "executable", "exe": "Z:/nope.exe"}}
            findings = svc.scan_windows_apps_health(apps, windows_dir, set())
            self.assertEqual(findings["missing_executables"], ["Broken"])
            self.assertEqual(findings["missing_placeholders"], ["Broken"])

    def test_flags_orphan_and_uninstalled_steam_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp)
            (windows_dir / "Orphan.pcgame").write_text("", encoding="utf-8")
            (windows_dir / "GameA.pcgame").write_text("", encoding="utf-8")
            (windows_dir / "GameB.pcgame").write_text("", encoding="utf-8")
            apps = {
                "GameA": {"type": "uri", "uri": "steam://run/1"},
                "GameB": {"type": "uri", "uri": "steam://run/1"},
            }
            findings = svc.scan_windows_apps_health(apps, windows_dir, installed_steam_ids_set=set())
            self.assertEqual(findings["orphan_placeholders"], ["Orphan"])
            self.assertEqual(sorted(findings["uninstalled_steam"]), ["GameA", "GameB"])
            self.assertEqual(len(findings["duplicate_steam_ids"]), 1)

    def test_healthy_setup_has_no_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp)
            (windows_dir / "Good.pcgame").write_text("", encoding="utf-8")
            apps = {"Good": {"type": "uri", "uri": "steam://run/1"}}
            findings = svc.scan_windows_apps_health(apps, windows_dir, installed_steam_ids_set={"1"})
            self.assertEqual(sum(len(v) for v in findings.values()), 0)


class ExportImportTests(unittest.TestCase):
    def test_build_and_parse_export_payload_round_trip(self):
        apps = {"MyApp": {"type": "executable", "exe": "C:/x.exe"}}
        payload = svc.build_export_payload(apps)
        self.assertEqual(svc.parse_import_payload(payload), apps)

    def test_parse_import_payload_accepts_bare_apps_dict(self):
        apps = {"MyApp": {"type": "executable"}}
        self.assertEqual(svc.parse_import_payload(apps), apps)

    def test_parse_import_payload_treats_non_dict_top_level_as_empty(self):
        # Matches manager.py's original behavior: a non-dict payload (e.g. a
        # JSON array) has nothing to extract "apps" from, so it's treated as
        # an empty mapping rather than a hard error.
        self.assertEqual(svc.parse_import_payload(["not", "a", "dict"]), {})

    def test_import_skips_duplicates_and_existing_steam_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            windows_dir = Path(tmp)
            apps = {"Existing": {"type": "executable", "exe": "C:/x.exe"}}
            incoming = {
                "Existing": {"type": "executable", "exe": "C:/y.exe"},
                "NewApp": {"type": "executable", "exe": "C:/z.exe"},
                "DupeSteam": {"type": "uri", "uri": "steam://run/440"},
            }
            added, skipped = svc.import_windows_apps(incoming, apps, windows_dir, existing_steam_ids={"440"})
            self.assertEqual(added, 1)
            self.assertEqual(skipped, 2)
            self.assertIn("NewApp", apps)
            self.assertTrue((windows_dir / "NewApp.pcgame").is_file())


class ResolveLaunchTests(unittest.TestCase):
    def test_resolve_uri_launch_rejects_invalid(self):
        with self.assertRaises(svc.WindowsAppsServiceError):
            svc.resolve_uri_launch({"uri": "not a uri"})

    def test_resolve_uri_launch_accepts_valid(self):
        self.assertEqual(svc.resolve_uri_launch({"uri": "steam://run/440"}), "steam://run/440")

    def test_resolve_executable_launch_rejects_missing_exe_field(self):
        with self.assertRaises(svc.WindowsAppsServiceError):
            svc.resolve_executable_launch({"exe": ""})

    def test_resolve_executable_launch_rejects_missing_file(self):
        with self.assertRaises(svc.WindowsAppsServiceError):
            svc.resolve_executable_launch({"exe": "Z:/does/not/exist.exe"})

    def test_resolve_executable_launch_rejects_invalid_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "app.exe"
            exe.write_bytes(b"x")
            with self.assertRaises(svc.WindowsAppsServiceError):
                svc.resolve_executable_launch({"exe": str(exe), "args": "not a list"})

    def test_resolve_executable_launch_defaults_working_dir_to_exe_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "app.exe"
            exe.write_bytes(b"x")
            executable, args, working_dir = svc.resolve_executable_launch({"exe": str(exe)})
            self.assertEqual(executable, exe)
            self.assertEqual(args, [])
            self.assertEqual(working_dir, exe.parent)


if __name__ == "__main__":
    unittest.main()
