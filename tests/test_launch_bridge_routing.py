"""
Unit tests for launch_bridge.find_emulator_for_package -- the routing logic
that decides which PC emulator profile a given (package, rom filename,
reported RetroArch core) resolves to. Pure function, no sockets/adb/AVD
involved, but importing launch_bridge.py does pull in its Windows-only
sibling modules (winapi.py, controller_bridge.py), so this only runs on
Windows, same as the project itself.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from launch_bridge import find_emulator_for_package
from shared.emulator_defaults import build_emulators_map  # noqa: E402 -- path set up above


class FindEmulatorForPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.emulators = build_emulators_map()

    def test_standalone_package_resolves_directly(self):
        profile = find_emulator_for_package("com.github.stenzek.duckstation", self.emulators, "game.chd", None)
        self.assertIsNotNone(profile)
        self.assertIn("duckstation-qt-x64-ReleaseLTCG.exe", profile["exe_names"])

    def test_unknown_package_resolves_to_none(self):
        profile = find_emulator_for_package("com.totally.unknown.thing", self.emulators, "game.bin", None)
        self.assertIsNone(profile)

    def test_retroarch_falls_back_to_extension_when_no_core_reported(self):
        profile = find_emulator_for_package("com.retroarch", self.emulators, "game.nes", None)
        self.assertIsNotNone(profile)
        self.assertIn("retroarch.exe", profile["exe_names"])
        self.assertIn("cores/fceumm_libretro.dll", profile["pre_args"])

    def test_retroarch_trusts_reported_core_over_extension_safety_net(self):
        # A Dreamcast game shipped as .chd would otherwise hit the PSX
        # safety-net extension entry -- the reported LIBRETRO core has to
        # win, or Dreamcast .chd games launch the wrong emulator entirely
        # (this was a real, live-reported bug; see shared/emulator_defaults
        # .py's RETROARCH_CORE_OVERRIDES docstring).
        profile = find_emulator_for_package(
            "com.retroarch", self.emulators, "game.chd", "flycast_libretro_android.so"
        )
        self.assertIsNotNone(profile)
        self.assertIn("flycast.exe", profile["exe_names"])

    def test_retroarch_core_without_dedicated_override_uses_generic_launch(self):
        profile = find_emulator_for_package(
            "com.retroarch", self.emulators, "game.nes", "fceumm_libretro_android.so"
        )
        self.assertIsNotNone(profile)
        self.assertIn("retroarch.exe", profile["exe_names"])
        self.assertIn("cores/fceumm_libretro.dll", profile["pre_args"])

    def test_retroarch_with_no_extension_and_no_core_resolves_to_none(self):
        profile = find_emulator_for_package("com.retroarch", self.emulators, None, None)
        self.assertIsNone(profile)


if __name__ == "__main__":
    unittest.main()
