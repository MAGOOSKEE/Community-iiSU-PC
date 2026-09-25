"""Unit tests for bridge/services/media_library_service.py's non-ADB logic
(registry load/save, filename/path derivation, size formatting). The
MediaBridge functions need a real ADB-connected device and are exercised
manually, same as the rest of this page's original implementation."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import media_library_service as svc


class RegistryTests(unittest.TestCase):
    def test_load_missing_registry_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(svc, "IIDB_REGISTRY_PATH", Path(tmp) / "installed_media.json"):
                self.assertEqual(svc.load_media_registry(), svc.media_registry_empty())

    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "installed_media.json"
            with patch.object(svc, "IIDB_REGISTRY_PATH", path), patch.object(svc, "IIDB_DIR", Path(tmp)):
                registry = {"version": 1, "games": {"a|b": {"assets": []}}}
                svc.save_media_registry(registry)
                self.assertEqual(svc.load_media_registry(), registry)

    def test_load_rejects_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "installed_media.json"
            path.write_text('{"version": 2, "games": {}}', encoding="utf-8")
            with patch.object(svc, "IIDB_REGISTRY_PATH", path):
                with self.assertRaises(svc.MediaLibraryServiceError):
                    svc.load_media_registry()

    def test_media_records_skips_non_dict_entries(self):
        registry = {
            "games": {
                "a|b": {"assets": [{"asset_type": "icon"}, "not a dict"]},
                "bad": "not a dict either",
            }
        }
        records = list(svc.media_records(registry))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][2], {"asset_type": "icon"})


class FilenameAndPathTests(unittest.TestCase):
    def test_media_remote_filename_known_types(self):
        self.assertEqual(svc.media_remote_filename("hero", 2, "jpg"), "hero_2.jpg")
        self.assertEqual(svc.media_remote_filename("icon", 1, "png"), "icon.png")
        self.assertEqual(svc.media_remote_filename("soundbite", 1, "wav"), "music.wav")

    def test_media_remote_filename_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            svc.media_remote_filename("bogus", 1, "png")

    def test_media_local_file_strips_legacy_library_prefix(self):
        asset = {"file": "library/tab/game/icon/abc123.png"}
        self.assertEqual(svc.media_local_file(asset), svc.IIDB_LIBRARY_DIR / "tab" / "game" / "icon" / "abc123.png")

    def test_media_local_file_normal_path(self):
        asset = {"file": "tab/game/icon/abc123.png"}
        self.assertEqual(svc.media_local_file(asset), svc.IIDB_LIBRARY_DIR / "tab" / "game" / "icon" / "abc123.png")

    def test_media_asset_remote_path_uses_stored_remote_filename(self):
        game = {"asset_dir": "/sdcard/games/mygame"}
        asset = {"remote_filename": "icon.png"}
        self.assertEqual(svc.media_asset_remote_path(game, asset), "/sdcard/games/mygame/icon.png")

    def test_media_asset_remote_path_derives_when_missing(self):
        game = {"asset_dir": "/sdcard/games/mygame/"}
        asset = {"asset_type": "icon", "slot": 1, "extension": "png"}
        self.assertEqual(svc.media_asset_remote_path(game, asset), "/sdcard/games/mygame/icon.png")


class HumanSizeTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(svc.human_size(500), "500 B")

    def test_kilobytes(self):
        self.assertEqual(svc.human_size(2048), "2.0 KB")

    def test_invalid_returns_empty(self):
        self.assertEqual(svc.human_size("not a number"), "")


class MediaCheckAssetTests(unittest.TestCase):
    def test_local_missing(self):
        asset = {"file": "does/not/exist.png"}
        status, _detail = svc.media_check_asset({}, asset)
        self.assertEqual(status, "LOCAL_MISSING")

    def test_local_changed_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "icon.png"
            local_path.write_bytes(b"hello")
            with patch.object(svc, "IIDB_LIBRARY_DIR", Path(tmp)):
                asset = {"file": "icon.png", "sha256": "0" * 64}
                status, _detail = svc.media_check_asset({}, asset)
                self.assertEqual(status, "LOCAL_CHANGED")

    def test_remote_missing_when_adb_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "icon.png"
            local_path.write_bytes(b"hello")
            with patch.object(svc, "IIDB_LIBRARY_DIR", Path(tmp)):
                asset = {"file": "icon.png", "sha256": svc.sha256_file(local_path), "asset_type": "icon", "extension": "png"}
                game = {"asset_dir": "/sdcard/x"}
                fake_result = MagicMock(returncode=1, stdout="", stderr="no such file")
                with patch.object(svc, "adb_shell_direct", return_value=fake_result):
                    status, _detail = svc.media_check_asset(game, asset)
                self.assertEqual(status, "REMOTE_MISSING")

    def test_ok_when_hashes_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "icon.png"
            local_path.write_bytes(b"hello")
            local_hash = svc.sha256_file(local_path)
            with patch.object(svc, "IIDB_LIBRARY_DIR", Path(tmp)):
                asset = {"file": "icon.png", "sha256": local_hash, "asset_type": "icon", "extension": "png"}
                game = {"asset_dir": "/sdcard/x"}
                fake_result = MagicMock(returncode=0, stdout=f"{local_hash}  icon.png\n", stderr="")
                with patch.object(svc, "adb_shell_direct", return_value=fake_result):
                    status, _detail = svc.media_check_asset(game, asset)
                self.assertEqual(status, "OK")


if __name__ == "__main__":
    unittest.main()
