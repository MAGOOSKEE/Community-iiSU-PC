"""Unit tests for bridge/services/android_backup_service.py: archive layout
and member validation (pure), plus backup/restore driven through a fake adb."""

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import android_backup_service as svc  # noqa: E402; path set up above


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class MemberValidationTests(unittest.TestCase):
    def test_accepts_known_labels(self):
        self.assertEqual(svc.parse_member("android/media/iiSULauncher/art/a.png"), ("media", "iiSULauncher/art/a.png"))
        self.assertEqual(svc.parse_member("android/data/x.db"), ("data", "x.db"))

    def test_rejects_unsafe_or_foreign_members(self):
        for name in (
            "android/media/../../etc/passwd",
            "/android/media/a.png",
            "android/other/a.png",
            "media/a.png",
            "android/media/",
            "android/media",
            "backup_manifest.txt",
            "android/media/C:/windows/x",
        ):
            self.assertIsNone(svc.parse_member(name), name)

    def test_backslashes_are_normalized(self):
        self.assertEqual(svc.parse_member("android\\media\\a\\b.png"), ("media", "a/b.png"))

    def test_the_media_bridge_inbox_is_never_restorable(self):
        self.assertIsNone(svc.parse_member("android/media/iiSULauncher/mediabridge/inbox/stage.png"))
        self.assertIsNotNone(svc.parse_member("android/media/iiSULauncher/mediabridge/other.png"))

    def test_exclusion_matches_whole_path_segments_only(self):
        self.assertTrue(svc.is_excluded("iiSULauncher/mediabridge/inbox"))
        self.assertTrue(svc.is_excluded("iiSULauncher/mediabridge/inbox/a.png"))
        self.assertFalse(svc.is_excluded("iiSULauncher/mediabridge/inbox2/a.png"))


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


class ArchiveTests(unittest.TestCase):
    def test_write_then_inspect_round_trip_and_inbox_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            media = _tree(tmp / "media_tree", {
                "iiSULauncher/art/a.png": b"A",
                "iiSULauncher/mediabridge/inbox/stage.png": b"temp",
                "settings.json": b"{}",
            })
            result = svc.BackupResult()
            result.skipped["data"] = "not readable"
            out = tmp / "b.zip"
            svc.write_archive(out, {"media": media}, result)

            self.assertEqual(result.backed_up, {"media": 2})
            found = svc.inspect_archive(out)
            self.assertEqual(sorted(found["media"]), ["iiSULauncher/art/a.png", "settings.json"])
            with zipfile.ZipFile(out) as zf:
                manifest = zf.read(svc.MANIFEST_NAME).decode()
            self.assertIn("media: 2 file(s)", manifest)
            self.assertIn("data: skipped (not readable)", manifest)

    def test_extract_ignores_unsafe_members_in_a_hand_edited_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = tmp / "evil.zip"
            with zipfile.ZipFile(out, "w") as zf:
                zf.writestr("android/media/ok.txt", "ok")
                zf.writestr("android/media/../../escape.txt", "bad")
                zf.writestr("android/elsewhere/x.txt", "bad")
            dest = tmp / "dest"
            folders = svc.extract_archive(out, dest)
            self.assertEqual(list(folders), ["media"])
            self.assertTrue((dest / "media" / "ok.txt").is_file())
            self.assertFalse((tmp / "escape.txt").exists())
            self.assertFalse((dest / "elsewhere").exists())

    def test_inspect_rejects_foreign_and_corrupt_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            foreign = tmp / "f.zip"
            with zipfile.ZipFile(foreign, "w") as zf:
                zf.writestr("bridge/config.json", "{}")
            with self.assertRaises(svc.AndroidBackupError):
                svc.inspect_archive(foreign)
            junk = tmp / "j.zip"
            junk.write_bytes(b"not a zip")
            with self.assertRaises(svc.AndroidBackupError):
                svc.inspect_archive(junk)


class FakeAdb:
    """Stands in for adb_command: `media` exists with files, `data` doesn't."""

    def __init__(self, existing=("media",), push_ok=True):
        self.existing = existing
        self.push_ok = push_ok
        self.calls: list[tuple] = []

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        if args[0] == "shell" and len(args) == 2 and args[1].startswith("[ -d"):
            label = next((k for k, v in svc.SOURCES.items() if v in args[1]), None)
            return _completed(stdout="yes\n" if label in self.existing else "")
        if args[0] == "pull":
            remote, dest = args[1], Path(args[2])
            folder = dest / Path(remote).name
            _tree(folder, {"iiSULauncher/art/a.png": b"A", "iiSULauncher/mediabridge/inbox/t.png": b"t"})
            return _completed()
        if args[0] == "push":
            return _completed() if self.push_ok else _completed(1, stderr="adb: error: no space\n")
        return _completed()


class BackupRestoreFlowTests(unittest.TestCase):
    def _patched(self, fake, ready=(True, "")):
        return (
            patch.object(svc, "adb_command", fake),
            patch.object(svc, "adb_device_ready", return_value=ready),
        )

    def test_backup_pulls_existing_sources_and_notes_the_missing_one(self):
        fake = FakeAdb(existing=("media",))
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            out = Path(tmp) / "b.zip"
            result = svc.create_backup(out)
            self.assertEqual(result.backed_up, {"media": 1})  # the inbox file was excluded
            self.assertIn("data", result.skipped)
            self.assertEqual(list(svc.inspect_archive(out)), ["media"])

    def test_backup_with_the_vm_down_raises_without_touching_adb_further(self):
        fake = FakeAdb()
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake, ready=(False, "VM is stopped"))[0], \
                self._patched(fake, ready=(False, "VM is stopped"))[1]:
            with self.assertRaisesRegex(svc.AndroidBackupError, "VM is stopped"):
                svc.create_backup(Path(tmp) / "b.zip")
        self.assertEqual(fake.calls, [])

    def test_backup_with_nothing_readable_raises(self):
        fake = FakeAdb(existing=())
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            with self.assertRaises(svc.AndroidBackupError):
                svc.create_backup(Path(tmp) / "b.zip")

    def test_restore_force_stops_iisu_then_pushes_contents_to_the_right_folder(self):
        fake = FakeAdb(existing=("media",))
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            archive = Path(tmp) / "b.zip"
            svc.create_backup(archive)
            fake.calls.clear()
            outcomes = svc.restore_backup(archive)
        self.assertEqual(outcomes, {"media": "restored"})
        verbs = [c[:3] for c in fake.calls]
        self.assertEqual(verbs[0], ("shell", "am", "force-stop"))
        push = next(c for c in fake.calls if c[0] == "push")
        self.assertTrue(push[1].endswith("/."))
        self.assertEqual(push[2], svc.SOURCES["media"])

    def test_restore_reports_a_failed_push_per_source(self):
        fake = FakeAdb(existing=("media",), push_ok=False)
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            archive = Path(tmp) / "b.zip"
            svc.create_backup(archive)
            outcomes = svc.restore_backup(archive)
        self.assertTrue(outcomes["media"].startswith("failed:"))
        self.assertIn("no space", outcomes["media"])

    def test_restore_of_a_bad_archive_never_reaches_adb(self):
        fake = FakeAdb()
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            junk = Path(tmp) / "j.zip"
            junk.write_bytes(b"nope")
            with self.assertRaises(svc.AndroidBackupError):
                svc.restore_backup(junk)
        self.assertEqual(fake.calls, [])


class SummaryTests(unittest.TestCase):
    def test_summary(self):
        result = svc.BackupResult(backed_up={"media": 3}, skipped={"data": "denied"})
        self.assertEqual(result.total_files, 3)
        self.assertEqual(result.summary(), "media: 3 file(s); data: skipped (denied)")


if __name__ == "__main__":
    unittest.main()
