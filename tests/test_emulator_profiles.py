"""Unit tests for bridge/emulator_profiles.py: normalization, migration of
pre-profile configs, and the emulator command line / config.ini a profile
produces."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import emulator_profiles as ep  # noqa: E402; path set up above


class NormalizeTests(unittest.TestCase):
    def test_empty_gives_defaults(self):
        self.assertEqual(ep.normalize_profile(None), ep.DEFAULT_PROFILE)

    def test_invalid_values_fall_back(self):
        profile = ep.normalize_profile({"gpu_mode": "nonsense", "accel": 7, "audio": "loud", "cores": "x", "ram_mb": -5})
        self.assertEqual(profile["gpu_mode"], "auto")
        self.assertEqual(profile["accel"], "auto")
        self.assertEqual(profile["audio"], "default")
        self.assertEqual(profile["cores"], 0)
        self.assertEqual(profile["ram_mb"], 0)

    def test_numbers_are_clamped(self):
        profile = ep.normalize_profile({"cores": 9999, "ram_mb": 10**9})
        self.assertEqual(profile["cores"], 64)
        self.assertEqual(profile["ram_mb"], 65536)

    def test_does_not_mutate_the_shared_default(self):
        ep.normalize_profile({"gpu_mode": "host"})["extra_args"] = "x"
        self.assertEqual(ep.DEFAULT_PROFILE["extra_args"], "")


class GetProfilesTests(unittest.TestCase):
    def test_pre_profile_config_synthesizes_default_from_legacy_gpu_mode(self):
        active, profiles = ep.get_profiles({"display": {"gpu_mode": "host"}})
        self.assertEqual(active, "Default")
        self.assertEqual(profiles["Default"]["gpu_mode"], "host")

    def test_config_without_anything_gets_auto(self):
        self.assertEqual(ep.active_profile({})["gpu_mode"], "auto")

    def test_active_name_that_does_not_exist_falls_back(self):
        config = {"emulator_profiles": {"active": "Gone", "profiles": {"A": {"gpu_mode": "host"}, "Default": {}}}}
        active, _profiles = ep.get_profiles(config)
        self.assertEqual(active, "Default")

    def test_no_default_picks_first(self):
        config = {"emulator_profiles": {"active": "Gone", "profiles": {"A": {}, "B": {}}}}
        self.assertEqual(ep.get_profiles(config)[0], "A")

    def test_blank_names_and_garbage_entries_are_dropped(self):
        config = {"emulator_profiles": {"active": "A", "profiles": {"  ": {}, "A": "not a dict", 5: {}}}}
        active, profiles = ep.get_profiles(config)
        self.assertEqual(list(profiles), ["A"])
        self.assertEqual(profiles["A"], ep.DEFAULT_PROFILE)

    def test_with_profiles_round_trips(self):
        config = ep.with_profiles({"keep": 1}, "Fast", {"Fast": {"cores": 6}, "Default": {}})
        self.assertEqual(config["keep"], 1)
        active, profiles = ep.get_profiles(config)
        self.assertEqual(active, "Fast")
        self.assertEqual(profiles["Fast"]["cores"], 6)


class BuildArgsTests(unittest.TestCase):
    def test_default_profile_is_just_gpu_auto(self):
        self.assertEqual(ep.build_emulator_args({}, is_windows=True), ["-gpu", "auto"])

    def test_linux_with_kvm_keeps_accel_on_as_before_profiles(self):
        self.assertEqual(ep.build_emulator_args({}, is_windows=False, kvm_present=True), ["-gpu", "auto", "-accel", "on"])

    def test_linux_without_kvm_has_no_accel_flag(self):
        self.assertEqual(ep.build_emulator_args({}, is_windows=False, kvm_present=False), ["-gpu", "auto"])

    def test_explicit_accel_wins_over_kvm_default(self):
        args = ep.build_emulator_args({"accel": "off"}, is_windows=False, kvm_present=True)
        self.assertEqual(args, ["-gpu", "auto", "-accel", "off"])

    def test_cores_ram_and_audio_off(self):
        args = ep.build_emulator_args({"gpu_mode": "host", "cores": 6, "ram_mb": 4096, "audio": "none"}, is_windows=True)
        self.assertEqual(args, ["-gpu", "host", "-cores", "6", "-memory", "4096", "-no-audio"])

    def test_no_input_does_not_pass_no_audio(self):
        self.assertNotIn("-no-audio", ep.build_emulator_args({"audio": "no_input"}, is_windows=True))

    def test_extra_args_are_split_and_appended(self):
        args = ep.build_emulator_args({"extra_args": "-feature -Vulkan -netdelay none"}, is_windows=True)
        self.assertEqual(args[-4:], ["-feature", "-Vulkan", "-netdelay", "none"])

    def test_windows_path_with_backslashes_survives(self):
        args = ep.split_extra_args(r'-skin "C:\Users\me\skin dir"', is_windows=True)
        self.assertEqual(args, ["-skin", r"C:\Users\me\skin dir"])

    def test_unbalanced_quote_yields_nothing_rather_than_raising(self):
        self.assertEqual(ep.split_extra_args('-skin "oops', is_windows=False), [])


class ConfigIniOverrideTests(unittest.TestCase):
    def test_default_restores_audio_on(self):
        self.assertEqual(ep.config_ini_overrides({}), {"hw.audioInput": "yes", "hw.audioOutput": "yes"})

    def test_no_input(self):
        self.assertEqual(ep.config_ini_overrides({"audio": "no_input"}), {"hw.audioInput": "no", "hw.audioOutput": "yes"})

    def test_none(self):
        self.assertEqual(ep.config_ini_overrides({"audio": "none"}), {"hw.audioInput": "no", "hw.audioOutput": "no"})


class EnvTests(unittest.TestCase):
    def test_parses_key_values_and_skips_junk(self):
        text = "# comment\nFOO=bar\n\nBAD LINE\nQEMU_AUDIO_DRV = dsound\nbad key=1\n"
        self.assertEqual(ep.parse_env(text), {"FOO": "bar", "QEMU_AUDIO_DRV": "dsound"})


class FingerprintTests(unittest.TestCase):
    def test_changes_when_the_profile_changes(self):
        self.assertNotEqual(ep.fingerprint({}), ep.fingerprint({"cores": 6}))

    def test_stable_for_equivalent_profiles(self):
        self.assertEqual(ep.fingerprint({}), ep.fingerprint(ep.DEFAULT_PROFILE))

    def test_describe_mentions_only_what_differs(self):
        text = ep.describe({"audio": "none", "cores": 6})
        self.assertIn("audio=none", text)
        self.assertIn("6 cores", text)
        self.assertNotIn("accel", text)


if __name__ == "__main__":
    unittest.main()
