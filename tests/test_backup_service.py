"""Unit tests for bridge/services/backup_service.py."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import backup_service as svc


class SafeMemberTests(unittest.TestCase):
    def test_rejects_path_traversal(self):
        self.assertFalse(svc.backup_safe_member("../etc/passwd"))
        self.assertFalse(svc.backup_safe_member("a/../../b"))
        self.assertFalse(svc.backup_safe_member("/etc/passwd"))

    def test_accepts_known_names(self):
        self.assertTrue(svc.backup_safe_member("bridge/config.json"))
        self.assertTrue(svc.backup_safe_member("windows_apps.json"))

    def test_rejects_unknown_names(self):
        self.assertFalse(svc.backup_safe_member("some_random_file.json"))


class CreateAndRestoreBackupTests(unittest.TestCase):
    def test_create_backup_then_inspect_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "config.json"
            config_file.write_text(json.dumps({"a": 1}), encoding="utf-8")
            dest = Path(tmp) / "backup.zip"

            with patch.object(svc, "_RESTORE_MAP", {"bridge/config.json": config_file}):
                svc.create_backup(dest, [(config_file, "bridge/config.json")])
                payloads = svc.inspect_backup(dest)

            self.assertIn("bridge/config.json", payloads)
            self.assertEqual(json.loads(payloads["bridge/config.json"]), {"a": 1})

    def test_inspect_backup_rejects_archive_with_no_known_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "backup.zip"
            with zipfile.ZipFile(dest, "w") as zf:
                zf.writestr("random_junk.txt", "hello")
            with self.assertRaises(svc.BackupServiceError):
                svc.inspect_backup(dest)

    def test_inspect_backup_rejects_invalid_json_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "backup.zip"
            with zipfile.ZipFile(dest, "w") as zf:
                zf.writestr("bridge/config.json", "{not valid json")
            with self.assertRaises(svc.BackupServiceError):
                svc.inspect_backup(dest)

    def test_inspect_backup_rejects_non_zip_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "not_a_zip.zip"
            dest.write_text("plain text", encoding="utf-8")
            with self.assertRaises(svc.BackupServiceError):
                svc.inspect_backup(dest)

    def test_apply_restore_creates_safety_copy_of_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            target.write_text(json.dumps({"old": True}), encoding="utf-8")
            safety_root = Path(tmp) / "restore_safety"

            with patch.object(svc, "_RESTORE_MAP", {"bridge/config.json": target}), patch.object(
                svc, "PROJECT_ROOT", Path(tmp)
            ):
                restored, safety_count, safety_dir = svc.apply_restore({"bridge/config.json": json.dumps({"new": True}).encode()})

            self.assertEqual(restored, 1)
            self.assertEqual(safety_count, 1)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": True})
            self.assertTrue((safety_dir / "bridge" / "config.json").is_file())

    def test_apply_restore_skips_safety_copy_when_target_is_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "windows_apps.json"

            with patch.object(svc, "_RESTORE_MAP", {"windows_apps.json": target}), patch.object(svc, "PROJECT_ROOT", Path(tmp)):
                restored, safety_count, _safety_dir = svc.apply_restore({"windows_apps.json": b"{}"})

            self.assertEqual(restored, 1)
            self.assertEqual(safety_count, 0)
            self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
