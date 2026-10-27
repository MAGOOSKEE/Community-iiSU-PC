"""
Unit tests for bridge/emulator_lookup.py: the routing logic that decides which PC
emulator profile a given (package, rom filename, reported RetroArch core)
resolves to, the executable and ROM search with its cache, the launch command
line, and the RetroArch core download. No sockets, adb or AVD involved, and no
Windows-only modules either, so this runs anywhere.
"""

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import emulator_lookup  # noqa: E402; path set up above
from emulator_lookup import (  # noqa: E402
    build_pc_launch_args,
    core_dll_from_pre_args,
    ensure_retroarch_core,
    find_emulator_for_package,
    find_executable,
    find_rom,
    friendly_emulator_name,
    load_path_cache,
    save_path_cache,
)
from shared.emulator_defaults import all_emulator_exe_names, build_emulators_map  # noqa: E402


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
        # safety-net extension entry, the reported LIBRETRO core has to
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


class CoreDllFromPreArgsTests(unittest.TestCase):
    def test_finds_core_dll_with_forward_slash(self):
        self.assertEqual(
            core_dll_from_pre_args(["-L", "cores/fceumm_libretro.dll", "-f"]),
            "fceumm_libretro.dll",
        )

    def test_finds_core_dll_with_backslash(self):
        self.assertEqual(
            core_dll_from_pre_args(["-L", "cores\\fceumm_libretro.dll", "-f"]),
            "fceumm_libretro.dll",
        )

    def test_no_cores_arg_returns_none(self):
        self.assertIsNone(core_dll_from_pre_args(["-f"]))

    def test_empty_pre_args_returns_none(self):
        self.assertIsNone(core_dll_from_pre_args([]))


class BuildPcLaunchArgsTests(unittest.TestCase):
    EXE = Path("C:/emu/emu.exe")
    ROM = Path("D:/roms/game.iso")

    def test_the_rom_goes_last_by_default(self):
        args = build_pc_launch_args({"pre_args": ["-f", "-x"]}, self.EXE, self.ROM)
        self.assertEqual(args, [str(self.EXE), "-f", "-x", str(self.ROM)])

    def test_rom_before_args_puts_the_rom_first(self):
        args = build_pc_launch_args({"pre_args": ["-f"], "rom_before_args": True}, self.EXE, self.ROM)
        self.assertEqual(args, [str(self.EXE), str(self.ROM), "-f"])

    def test_no_rom_leaves_just_the_flags(self):
        args = build_pc_launch_args({"pre_args": ["-f"], "rom_before_args": True}, self.EXE, None)
        self.assertEqual(args, [str(self.EXE), "-f"])


class PathCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = Path(self._tmp.name) / ".path_cache.json"
        patcher = patch.object(emulator_lookup, "PATH_CACHE_PATH", self.cache_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_missing_file_gives_an_empty_cache(self):
        self.assertEqual(load_path_cache(), {"executables": {}, "roms": {}})

    def test_a_corrupt_file_gives_an_empty_cache(self):
        self.cache_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(load_path_cache(), {"executables": {}, "roms": {}})

    def test_missing_sections_are_filled_in(self):
        self.cache_path.write_text(json.dumps({"executables": {"a": "b"}}), encoding="utf-8")
        self.assertEqual(load_path_cache(), {"executables": {"a": "b"}, "roms": {}})

    def test_save_then_load_round_trips(self):
        save_path_cache({"executables": {"x": "y"}, "roms": {"r": "s"}})
        self.assertEqual(load_path_cache(), {"executables": {"x": "y"}, "roms": {"r": "s"}})

    def test_saving_to_an_unwritable_place_is_ignored(self):
        with patch.object(emulator_lookup, "PATH_CACHE_PATH", Path(self._tmp.name)):  # a directory
            save_path_cache({"executables": {}, "roms": {}})


class FindExecutableAndRomTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        which = patch.object(emulator_lookup.shutil, "which", return_value=None)
        which.start()
        self.addCleanup(which.stop)

    def _touch(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    @staticmethod
    def _cache() -> dict:
        return {"executables": {}, "roms": {}}

    def test_finds_an_executable_a_few_folders_down(self):
        exe = self._touch("Emulators", "Duck", "duck.exe")
        self.assertEqual(find_executable(["duck.exe"], [self.root], self._cache()), exe)

    def test_a_shallower_match_beats_a_deeper_one(self):
        shallow = self._touch("a", "duck.exe")
        self._touch("b", "c", "d", "duck.exe")
        self.assertEqual(find_executable(["duck.exe"], [self.root], self._cache()), shallow)

    def test_nothing_deeper_than_the_depth_cap_is_found(self):
        self._touch("1", "2", "3", "4", "5", "6", "duck.exe")
        self.assertIsNone(find_executable(["duck.exe"], [self.root], self._cache()))

    def test_skipped_folders_are_not_searched(self):
        self._touch(".git", "duck.exe")
        self.assertIsNone(find_executable(["duck.exe"], [self.root], self._cache()))

    def test_a_missing_search_root_is_ignored(self):
        self.assertIsNone(find_executable(["duck.exe"], [self.root / "nope"], self._cache()))

    def test_the_path_wins_over_the_search_roots(self):
        on_path = self._touch("bin", "duck.exe")
        self._touch("elsewhere", "duck.exe")
        with patch.object(emulator_lookup.shutil, "which", return_value=str(on_path)):
            self.assertEqual(find_executable(["duck.exe"], [self.root / "elsewhere"], self._cache()), on_path)

    def test_a_hit_is_cached_and_reused(self):
        exe = self._touch("duck.exe")
        cache = self._cache()
        find_executable(["duck.exe"], [self.root], cache)
        self.assertEqual(list(cache["executables"].values()), [str(exe)])
        with patch.object(emulator_lookup, "_find_by_name") as scan:
            self.assertEqual(find_executable(["duck.exe"], [self.root], cache), exe)
        scan.assert_not_called()

    def test_a_cached_path_that_vanished_is_searched_again(self):
        old = self._touch("old", "duck.exe")
        cache = self._cache()
        find_executable(["duck.exe"], [self.root], cache)
        old.unlink()
        moved = self._touch("new", "duck.exe")
        self.assertEqual(find_executable(["duck.exe"], [self.root], cache), moved)

    def test_a_failed_search_clears_the_stale_entry(self):
        exe = self._touch("duck.exe")
        cache = self._cache()
        find_executable(["duck.exe"], [self.root], cache)
        exe.unlink()
        self.assertIsNone(find_executable(["duck.exe"], [self.root], cache))
        self.assertEqual(cache["executables"], {})

    def test_changing_the_search_roots_does_not_reuse_the_old_entry(self):
        first = self._touch("one", "duck.exe")
        cache = self._cache()
        find_executable(["duck.exe"], [self.root / "one"], cache)
        second = self._touch("two", "duck.exe")
        self.assertEqual(find_executable(["duck.exe"], [self.root / "two"], cache), second)
        self.assertNotEqual(first, second)

    def test_find_rom_finds_and_caches_a_rom(self):
        rom = self._touch("snes", "Game.sfc")
        cache = self._cache()
        self.assertEqual(find_rom("Game.sfc", self.root, cache), rom)
        self.assertEqual(cache["roms"], {f"Game.sfc::{self.root}": str(rom)})

    def test_find_rom_with_a_missing_folder_or_file_is_none(self):
        self.assertIsNone(find_rom("Game.sfc", self.root / "nope", self._cache()))
        self.assertIsNone(find_rom("Game.sfc", self.root, self._cache()))

    def test_find_rom_follows_a_moved_rom(self):
        old = self._touch("a", "Game.sfc")
        cache = self._cache()
        find_rom("Game.sfc", self.root, cache)
        old.unlink()
        moved = self._touch("b", "Game.sfc")
        self.assertEqual(find_rom("Game.sfc", self.root, cache), moved)


class EnsureRetroarchCoreTests(unittest.TestCase):
    CORE = "fceumm_libretro.dll"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.retroarch = Path(self._tmp.name)

    @staticmethod
    def _response(payload: bytes):
        response = MagicMock()
        response.read.return_value = payload
        response.__enter__.return_value = response
        return response

    @staticmethod
    def _zip_with(name: str, data: bytes) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(name, data)
        return buffer.getvalue()

    def test_an_installed_core_needs_no_download(self):
        (self.retroarch / "cores").mkdir()
        (self.retroarch / "cores" / self.CORE).write_bytes(b"core")
        with patch.object(emulator_lookup.urllib.request, "urlopen") as urlopen:
            self.assertTrue(ensure_retroarch_core(self.retroarch, self.CORE))
        urlopen.assert_not_called()

    def test_a_missing_core_is_downloaded_and_unzipped(self):
        payload = self._zip_with(self.CORE, b"fresh")
        with patch.object(emulator_lookup.urllib.request, "urlopen", return_value=self._response(payload)) as urlopen:
            self.assertTrue(ensure_retroarch_core(self.retroarch, self.CORE))
        self.assertEqual((self.retroarch / "cores" / self.CORE).read_bytes(), b"fresh")
        self.assertIn(self.CORE, urlopen.call_args.args[0])

    def test_a_failed_download_returns_false_and_writes_nothing(self):
        with patch.object(emulator_lookup.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertFalse(ensure_retroarch_core(self.retroarch, self.CORE))
        self.assertFalse((self.retroarch / "cores" / self.CORE).exists())

    def test_a_zip_without_the_core_returns_false(self):
        payload = self._zip_with("something_else.dll", b"x")
        with patch.object(emulator_lookup.urllib.request, "urlopen", return_value=self._response(payload)):
            self.assertFalse(ensure_retroarch_core(self.retroarch, self.CORE))


class FriendlyEmulatorNameTests(unittest.TestCase):
    def test_a_curated_executable_gets_its_label(self):
        label, exe_names = all_emulator_exe_names()[0]
        self.assertEqual(friendly_emulator_name(Path("C:/anywhere") / exe_names[0]), label)

    def test_an_unknown_executable_falls_back_to_its_name(self):
        self.assertEqual(friendly_emulator_name(Path("C:/odd/mystery-emu.exe")), "mystery-emu")


if __name__ == "__main__":
    unittest.main()
