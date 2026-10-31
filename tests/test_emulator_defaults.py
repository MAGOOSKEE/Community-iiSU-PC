"""
Unit tests for shared/emulator_defaults.py's pure mapping logic, no
Windows APIs, no AVD, no adb involved, so these run anywhere Python does.

Run with: python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.emulator_defaults import (
    RETROARCH_PACKAGE,
    STANDALONE_DEFAULTS,
    all_emulator_exe_names,
    all_stub_packages,
    build_emulators_map,
    describe_profile,
    retroarch_core_dll_for_android_core,
    standalone_profile_for_core_dll,
    upgrade_outdated_defaults,
)


class OutdatedDefaultTests(unittest.TestCase):
    OLD_CITRON = {"exe_names": ["citron.exe"], "pre_args": ["-f"]}

    def test_citron_gets_its_game_after_g(self):
        # citron.exe -f <game> opened Citron without starting the game.
        self.assertEqual(build_emulators_map()["org.citron.citron_emu"]["pre_args"], ["-f", "-g"])

    def test_an_untouched_old_default_is_upgraded_and_an_edited_one_kept(self):
        edited = {"exe_names": ["citron.exe"], "pre_args": ["-f", "--portable"]}
        upgraded = upgrade_outdated_defaults({
            "org.citron.citron_emu": dict(self.OLD_CITRON), "org.yuzu.yuzu_emu": edited,
            "com.github.stenzek.duckstation": {"exe_names": ["duckstation.exe"], "pre_args": ["-fullscreen"]},
        })
        self.assertEqual(upgraded["org.citron.citron_emu"]["pre_args"], ["-f", "-g"])
        self.assertIs(upgraded["org.yuzu.yuzu_emu"], edited)
        self.assertEqual(upgraded["com.github.stenzek.duckstation"]["pre_args"], ["-fullscreen"])

    def test_loading_the_config_applies_it(self):
        import json
        import tempfile
        from unittest.mock import patch

        sys.path.insert(0, str(PROJECT_ROOT / "bridge"))
        import bridge_config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"emulators": {"org.citron.citron_emu": self.OLD_CITRON}}), encoding="utf-8")
            with patch.object(bridge_config, "CONFIG_PATH", path):
                config = bridge_config.load_config()
        self.assertEqual(config["emulators"]["org.citron.citron_emu"]["pre_args"], ["-f", "-g"])


class EmulatorScanTests(unittest.TestCase):
    def test_steam_is_not_scanned_for_as_an_emulator(self):
        # Steam games start through steam:// links, so Setup's scan listed
        # Steam as a missing emulator for no reason.
        labels = [label for label, _exe_names in all_emulator_exe_names()]
        self.assertNotIn("Steam", labels)
        self.assertIn("DuckStation", labels)
        self.assertIn("RetroArch", labels)

    def test_winlator_gets_a_stub_so_iisu_hands_windows_games_their_path(self):
        # Without it iiSU 7.5 fell through to GameNative, which reads a game ID
        # out of the (empty) placeholder, and refused every Windows game.
        self.assertIn(("com.winlator.cmod", "Winlator"), all_stub_packages())
        self.assertNotIn("com.winlator.cmod", build_emulators_map(), "the bridge handles Winlator itself")
        self.assertNotIn("Winlator", [label for label, _exe in all_emulator_exe_names()])

    def test_steam_still_gets_its_redirector_app_and_mapping(self):
        self.assertIn("app.gamenative", [package for package, _label in all_stub_packages()])
        self.assertEqual(build_emulators_map()["app.gamenative"], {"exe_names": ["steam.exe"], "pre_args": []})


class BuildEmulatorsMapTests(unittest.TestCase):
    def test_every_standalone_package_present(self):
        emulators = build_emulators_map()
        for entry in STANDALONE_DEFAULTS:
            self.assertIn(entry["package"], emulators)

    def test_retroarch_entry_is_by_extension(self):
        emulators = build_emulators_map()
        self.assertIn("by_extension", emulators[RETROARCH_PACKAGE])

    def test_rpcs3_keeps_rom_before_args(self):
        emulators = build_emulators_map()
        self.assertTrue(emulators["aenu.aps3e"].get("rom_before_args"))

    def test_shared_package_collapses_to_one_entry(self):
        # GameCube and Wii both route through org.dolphinemu.dolphinemu,
        # collapsing to the same dict entry is intentional, not a bug.
        emulators = build_emulators_map()
        gc = next(e for e in STANDALONE_DEFAULTS if e["console"] == "gc")
        wii = next(e for e in STANDALONE_DEFAULTS if e["console"] == "wii")
        self.assertEqual(gc["package"], wii["package"])
        self.assertIn(gc["package"], emulators)


class RetroarchCoreTranslationTests(unittest.TestCase):
    def test_translates_android_core_to_windows_dll(self):
        self.assertEqual(
            retroarch_core_dll_for_android_core("fceumm_libretro_android.so"),
            "fceumm_libretro.dll",
        )

    def test_applies_gpu_backend_override(self):
        self.assertEqual(
            retroarch_core_dll_for_android_core("mupen64plus_next_gles3_libretro_android.so"),
            "mupen64plus_next_libretro.dll",
        )

    def test_the_full_device_path_iisu_7_5_sends_is_reduced_to_the_file_name(self):
        # Seen live: -L cores//data/data/com.retroarch/cores/snes9x_libretro.dll,
        # and RetroArch exited at once for every game.
        for sent, expected in (
            ("/data/data/com.retroarch/cores/snes9x_libretro_android.so", "snes9x_libretro.dll"),
            ("/data/data/com.retroarch/cores/mupen64plus_next_gles3_libretro_android.so", "mupen64plus_next_libretro.dll"),
        ):
            with self.subTest(sent=sent):
                self.assertEqual(retroarch_core_dll_for_android_core(sent), expected)

    def test_non_libretro_android_filename_returns_none(self):
        self.assertIsNone(retroarch_core_dll_for_android_core("not_a_core.so"))
        self.assertIsNone(retroarch_core_dll_for_android_core(""))

    def test_flycast_core_has_a_standalone_override(self):
        core_dll = retroarch_core_dll_for_android_core("flycast_libretro_android.so")
        self.assertEqual(core_dll, "flycast_libretro.dll")
        override = standalone_profile_for_core_dll(core_dll)
        self.assertIsNotNone(override)
        self.assertIn("flycast.exe", override["exe_names"])

    def test_core_without_override_returns_none(self):
        core_dll = retroarch_core_dll_for_android_core("fceumm_libretro_android.so")
        self.assertIsNone(standalone_profile_for_core_dll(core_dll))


class DescribeProfileTests(unittest.TestCase):
    def test_flat_profile(self):
        exe, flags = describe_profile({"exe_names": ["duckstation-qt-x64-ReleaseLTCG.exe"], "pre_args": ["-fullscreen"]})
        self.assertEqual(exe, "duckstation-qt-x64-ReleaseLTCG.exe")
        self.assertEqual(flags, "-fullscreen")

    def test_by_extension_profile_summarizes_instead_of_empty(self):
        emulators = build_emulators_map()
        exe, flags = describe_profile(emulators[RETROARCH_PACKAGE])
        self.assertNotEqual(exe, "")
        self.assertIn("varies by file extension", flags)


if __name__ == "__main__":
    unittest.main()
