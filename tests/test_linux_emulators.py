"""Finding emulators on Linux from mappings that name Windows executables
(issue #14: EmuDeck on SteamOS found games but launched none)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import emulator_lookup  # noqa: E402
from shared import emulator_defaults, linux_emulators  # noqa: E402


def _touch(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"")
    return path


class LinuxNamesTests(unittest.TestCase):
    def test_every_default_emulator_has_linux_names(self):
        for _label, exe_names in emulator_defaults.all_emulator_exe_names():
            for exe_name in exe_names:
                self.assertIn(exe_name.casefold(), linux_emulators.LINUX_EQUIVALENTS, exe_name)

    def test_emudeck_launcher_script_matches(self):
        matches = linux_emulators.linux_matcher(["duckstation-qt-x64-ReleaseLTCG.exe"])
        self.assertTrue(matches("duckstation.sh"))
        self.assertFalse(matches("pcsx2-qt.sh"))

    def test_flatpak_launcher_matches(self):
        self.assertTrue(linux_emulators.linux_matcher(["pcsx2-qt.exe"])("net.pcsx2.PCSX2"))

    def test_versioned_appimage_matches_by_how_it_starts(self):
        matches = linux_emulators.linux_matcher(["pcsx2-qt.exe"])
        self.assertTrue(matches("pcsx2-v2.4.0-linux-appimage-x64-Qt.AppImage"))
        self.assertFalse(matches("pcsx2-readme.txt"))
        self.assertFalse(matches("rpcs3-v0.0.37-linux64.AppImage"))

    def test_names_compare_without_regard_to_case(self):
        self.assertTrue(linux_emulators.linux_matcher(["melonDS.exe"])("melonds"))

    def test_unknown_exe_falls_back_to_its_stem(self):
        matches = linux_emulators.linux_matcher(["MyEmu.exe"])
        for name in ("MyEmu", "myemu", "myemu.sh", "MyEmu-1.2-x86_64.AppImage"):
            self.assertTrue(matches(name), name)

    def test_a_linux_name_in_a_mapping_is_used_as_written(self):
        matches = linux_emulators.linux_matcher(["my-launcher.sh"])
        self.assertTrue(matches("my-launcher.sh"))
        self.assertFalse(matches("my-launcher"))

    def test_core_argument_becomes_a_bare_so_name(self):
        self.assertEqual(linux_emulators.linux_core_argument("cores/snes9x_libretro.dll"), "snes9x_libretro.so")
        self.assertEqual(linux_emulators.linux_core_argument("cores\\mgba_libretro.dll"), "mgba_libretro.so")
        self.assertEqual(linux_emulators.linux_core_argument("-fullscreen"), "-fullscreen")


class FindExecutableOnLinuxTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # Neither this PC's PATH nor its home folder takes part.
        for target, value in (("shutil.which", None), ("default_linux_search_roots", [])):
            if target == "shutil.which":
                patcher = patch.object(emulator_lookup.shutil, "which", return_value=value)
            else:
                patcher = patch.object(emulator_lookup, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _find(self, names, roots):
        return emulator_lookup.find_executable(names, roots, {"executables": {}}, is_windows=False)

    def test_finds_an_emudeck_launcher_for_a_windows_mapping(self):
        script = _touch(self.root / "tools" / "launchers", "duckstation.sh")
        self.assertEqual(self._find(["duckstation-qt-x64-ReleaseLTCG.exe"], [self.root]), script)

    def test_finds_an_appimage_for_a_windows_mapping(self):
        image = _touch(self.root / "Applications", "rpcs3-v0.0.37-linux64.AppImage")
        self.assertEqual(self._find(["rpcs3.exe"], [self.root]), image)

    def test_the_windows_name_itself_is_not_needed(self):
        _touch(self.root, "unrelated.sh")
        self.assertIsNone(self._find(["rpcs3.exe"], [self.root]))

    def test_the_usual_linux_folders_are_searched_without_being_configured(self):
        usual = self.root / "flatpak-exports"
        launcher = _touch(usual, "net.pcsx2.PCSX2")
        with patch.object(emulator_lookup, "default_linux_search_roots", return_value=[usual]):
            self.assertEqual(self._find(["pcsx2-qt.exe"], []), launcher)

    def test_a_configured_folder_wins_over_the_path(self):
        chosen = _touch(self.root, "pcsx2-qt.sh")
        on_path = _touch(self.root / "elsewhere", "pcsx2-qt")
        with patch.object(emulator_lookup.shutil, "which", return_value=str(on_path)):
            self.assertEqual(self._find(["pcsx2-qt.exe"], [self.root / "pcsx2-qt.sh"]), on_path)
            self.assertEqual(self._find(["pcsx2-qt.exe"], [self.root]), chosen)

    def test_windows_lookup_still_needs_the_exact_name(self):
        _touch(self.root, "duckstation.sh")
        found = emulator_lookup.find_executable(
            ["duckstation-qt-x64-ReleaseLTCG.exe"], [self.root], {"executables": {}}, is_windows=True
        )
        self.assertIsNone(found)


class LaunchArgsOnLinuxTests(unittest.TestCase):
    PROFILE = {"exe_names": ["retroarch.exe"], "pre_args": ["-L", "cores/snes9x_libretro.dll", "-f"]}

    def test_retroarch_core_is_passed_the_linux_way(self):
        args = emulator_lookup.build_pc_launch_args(self.PROFILE, Path("/usr/bin/retroarch"), Path("/roms/a.sfc"), is_windows=False)
        self.assertEqual(args[1:4], ["-L", "snes9x_libretro.so", "-f"])

    def test_windows_arguments_are_unchanged(self):
        args = emulator_lookup.build_pc_launch_args(self.PROFILE, Path("retroarch.exe"), None, is_windows=True)
        self.assertEqual(args[1:], ["-L", "cores/snes9x_libretro.dll", "-f"])


if __name__ == "__main__":
    unittest.main()
