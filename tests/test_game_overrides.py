"""Unit tests for bridge/game_overrides.py."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import game_overrides as go  # noqa: E402; path set up above
from launch_bridge import build_pc_launch_args  # noqa: E402

EMULATORS = {
    "com.duck": {"exe_names": ["duckstation.exe"], "pre_args": ["-fullscreen"]},
    "com.rpcs3": {"exe_names": ["rpcs3.exe"], "pre_args": ["--no-gui"], "rom_before_args": True},
    "com.retroarch": {"by_extension": {".nes": {"exe_names": ["retroarch.exe"], "pre_args": []}}},
}


class NormalizeTests(unittest.TestCase):
    def test_unknown_keys_are_dropped_and_values_stripped(self):
        result = go.normalize_override({"emulator": " com.duck ", "evil": "x", "extra_args": None})
        self.assertEqual(result, {"emulator": "com.duck", "extra_args": "", "env": "", "pre_launch": ""})

    def test_empty_detection(self):
        self.assertTrue(go.is_empty(None))
        self.assertTrue(go.is_empty({"extra_args": "  "}))
        self.assertFalse(go.is_empty({"env": "A=1"}))


class LookupTests(unittest.TestCase):
    CONFIG = {"game_overrides": {"Game One.m3u": {"extra_args": "-x"}, "empty.iso": {}}}

    def test_exact_match(self):
        self.assertEqual(go.get_override(self.CONFIG, "Game One.m3u")["extra_args"], "-x")

    def test_case_insensitive_fallback(self):
        self.assertEqual(go.get_override(self.CONFIG, "game one.M3U")["extra_args"], "-x")

    def test_empty_override_counts_as_none(self):
        self.assertIsNone(go.get_override(self.CONFIG, "empty.iso"))

    def test_missing_cases(self):
        self.assertIsNone(go.get_override(self.CONFIG, "other.iso"))
        self.assertIsNone(go.get_override(self.CONFIG, None))
        self.assertIsNone(go.get_override({}, "x"))
        self.assertIsNone(go.get_override({"game_overrides": "garbage"}, "x"))


class SetOverrideTests(unittest.TestCase):
    def test_sets_and_does_not_mutate_the_input(self):
        original = {"keep": 1}
        updated = go.set_override(original, "a.iso", {"extra_args": "-x"})
        self.assertEqual(updated["game_overrides"]["a.iso"]["extra_args"], "-x")
        self.assertNotIn("game_overrides", original)

    def test_empty_override_removes_the_entry_and_the_block(self):
        config = go.set_override({}, "a.iso", {"extra_args": "-x"})
        config = go.set_override(config, "a.iso", {})
        self.assertNotIn("game_overrides", config)

    def test_differently_cased_key_is_replaced_not_shadowed(self):
        config = go.set_override({}, "A.ISO", {"extra_args": "-old"})
        config = go.set_override(config, "a.iso", {"extra_args": "-new"})
        self.assertEqual(list(config["game_overrides"]), ["a.iso"])
        self.assertEqual(go.get_override(config, "A.ISO")["extra_args"], "-new")


class ApplyTests(unittest.TestCase):
    def test_selectable_excludes_by_extension_entries(self):
        self.assertEqual(go.selectable_emulators(EMULATORS), ["com.duck", "com.rpcs3"])

    def test_extra_args_are_appended_to_the_profile(self):
        profile, notes = go.apply_to_profile(EMULATORS["com.duck"], {"extra_args": "-batch -nogui"}, EMULATORS, is_windows=True)
        self.assertEqual(profile["pre_args"], ["-fullscreen", "-batch", "-nogui"])
        self.assertTrue(notes)

    def test_emulator_override_swaps_the_whole_profile(self):
        profile, _notes = go.apply_to_profile(EMULATORS["com.duck"], {"emulator": "com.rpcs3"}, EMULATORS, is_windows=True)
        self.assertEqual(profile["exe_names"], ["rpcs3.exe"])

    def test_emulator_and_args_combine(self):
        profile, _ = go.apply_to_profile(EMULATORS["com.duck"], {"emulator": "com.rpcs3", "extra_args": "--x"}, EMULATORS, is_windows=True)
        self.assertEqual(profile["pre_args"], ["--no-gui", "--x"])

    def test_unknown_or_unselectable_emulator_is_ignored_with_a_note(self):
        for target in ("com.missing", "com.retroarch"):
            profile, notes = go.apply_to_profile(EMULATORS["com.duck"], {"emulator": target}, EMULATORS, is_windows=True)
            self.assertEqual(profile["exe_names"], ["duckstation.exe"])
            self.assertIn("ignored", notes[0])

    def test_input_profile_is_not_mutated(self):
        go.apply_to_profile(EMULATORS["com.duck"], {"extra_args": "-x"}, EMULATORS, is_windows=True)
        self.assertEqual(EMULATORS["com.duck"]["pre_args"], ["-fullscreen"])

    def test_extra_args_land_before_the_rom_for_a_trailing_rom_emulator(self):
        profile, _ = go.apply_to_profile(EMULATORS["com.duck"], {"extra_args": "-x"}, EMULATORS, is_windows=True)
        argv = build_pc_launch_args(profile, Path("d.exe"), Path("game.bin"))
        self.assertEqual(argv, ["d.exe", "-fullscreen", "-x", "game.bin"])

    def test_extra_args_land_after_the_rom_for_a_rom_first_emulator(self):
        profile, _ = go.apply_to_profile(EMULATORS["com.rpcs3"], {"extra_args": "--x"}, EMULATORS, is_windows=True)
        argv = build_pc_launch_args(profile, Path("r.exe"), Path("game.bin"))
        self.assertEqual(argv, ["r.exe", "game.bin", "--no-gui", "--x"])


class EnvTests(unittest.TestCase):
    def test_no_env_means_inherit(self):
        self.assertIsNone(go.launch_env({"env": ""}))

    def test_env_is_layered_over_the_base(self):
        env = go.launch_env({"env": "A=1\n# c\nB=2"}, base={"PATH": "p", "A": "old"})
        self.assertEqual(env, {"PATH": "p", "A": "1", "B": "2"})


class PreLaunchTests(unittest.TestCase):
    def test_blank_is_a_noop(self):
        self.assertEqual(go.run_pre_launch("  ", is_windows=False), (True, ""))

    def test_success(self):
        ok, message = go.run_pre_launch(f'"{sys.executable}" -c "pass"', is_windows=sys.platform == "win32")
        self.assertTrue(ok, message)

    def test_nonzero_exit_is_reported_not_raised(self):
        ok, message = go.run_pre_launch(f'"{sys.executable}" -c "import sys; sys.exit(3)"', is_windows=sys.platform == "win32")
        self.assertFalse(ok)
        self.assertIn("exited 3", message)

    def test_missing_program_is_reported_not_raised(self):
        ok, message = go.run_pre_launch("definitely-not-a-real-program-xyz", is_windows=False)
        self.assertFalse(ok)
        self.assertIn("launching anyway", message)

    def test_timeout_is_reported_not_raised(self):
        ok, message = go.run_pre_launch(f'"{sys.executable}" -c "import time; time.sleep(5)"', is_windows=sys.platform == "win32", timeout=0.5)
        self.assertFalse(ok)
        self.assertIn("timed out", message)


if __name__ == "__main__":
    unittest.main()
