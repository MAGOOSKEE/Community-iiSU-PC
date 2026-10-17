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

    DF_HEALTHY = "Filesystem Size Used Avail Use% Mounted on\n/dev/block/dm-55 16G 1.0G 15G 7% /data\n"
    DF_FULL = "Filesystem Size Used Avail Use% Mounted on\n/dev/block/dm-55 5.8G 5.8G 1M 100% /data\n"

    def __init__(self, existing=("media",), push_ok=True, extract_ok=True, df=None):
        self.existing = existing
        self.push_ok = push_ok
        self.extract_ok = extract_ok
        self.df = df or self.DF_HEALTHY
        self.calls: list[tuple] = []
        self.pushed_tar_members: list[str] = []

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        if args[0] == "shell" and len(args) == 2 and args[1].startswith("df -h"):
            return _completed(stdout=self.df)
        if args[0] == "shell" and len(args) == 2 and "tar xf" in args[1]:
            return _completed() if self.extract_ok else _completed(1, stderr="tar: bad archive\n")
        if args[0] == "push" and args[2].endswith(".tar"):
            if self.push_ok:
                import tarfile
                with tarfile.open(args[1]) as tar:
                    self.pushed_tar_members += tar.getnames()
                return _completed()
            return _completed(1, stderr="adb: error: no space\n[  0%] /data/local/tmp/x.tar: 100%\n")
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

    def test_restore_disables_iisu_around_the_push_and_pushes_contents_to_the_right_folder(self):
        fake = FakeAdb(existing=("media",))
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            archive = Path(tmp) / "b.zip"
            svc.create_backup(archive)
            fake.calls.clear()
            outcomes = svc.restore_backup(archive)
        self.assertEqual(outcomes, {"media": "restored"})
        verbs = [c[:3] for c in fake.calls]
        # iiSU is the Home app and relaunches itself after a force-stop, racing
        # the push; it has to be disabled, not just stopped.
        self.assertEqual(verbs[0], ("shell", "pm", "disable-user"))
        self.assertEqual(verbs[-1], ("shell", "pm", "enable"))
        self.assertNotIn(("shell", "am", "force-stop"), verbs)
        # One tar pushed to the VM's tmp dir (not file by file), with paths
        # relative to the destination, then extracted there and cleaned up.
        pushes = [c for c in fake.calls if c[0] == "push"]
        self.assertEqual(len(pushes), 1)
        self.assertTrue(pushes[0][2].startswith("/data/local/tmp/") and pushes[0][2].endswith(".tar"))
        self.assertEqual(fake.pushed_tar_members, ["iiSULauncher/art/a.png"])
        extract = next(c for c in fake.calls if c[0] == "shell" and len(c) == 2 and "tar xf" in c[1])
        self.assertIn(svc.SOURCES["media"], extract[1])
        cleanup = next(c for c in fake.calls if c[0] == "shell" and len(c) == 2 and c[1].startswith("rm -f"))
        self.assertGreater(fake.calls.index(cleanup), fake.calls.index(extract))
        self.assertLess(fake.calls.index(pushes[0]), fake.calls.index(extract))
        self.assertLess(verbs.index(("shell", "pm", "disable-user")), fake.calls.index(pushes[0]))

    def test_a_failed_extract_is_reported_and_the_tar_is_still_removed(self):
        fake = FakeAdb(existing=("media",), extract_ok=False)
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            archive = Path(tmp) / "b.zip"
            svc.create_backup(archive)
            fake.calls.clear()
            outcomes = svc.restore_backup(archive)
        self.assertIn("bad archive", outcomes["media"])
        self.assertTrue(any(c[0] == "shell" and len(c) == 2 and c[1].startswith("rm -f") for c in fake.calls))

    def test_restore_refuses_when_the_vm_hasnt_the_space_and_pushes_nothing(self):
        fake = FakeAdb(existing=("media",))
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            archive = Path(tmp) / "b.zip"
            svc.create_backup(archive)
            fake.calls.clear()
            fake.df = FakeAdb.DF_FULL
            outcomes = svc.restore_backup(archive)
        self.assertIn("not enough free space", outcomes["media"])
        self.assertFalse(any(c[0] == "push" for c in fake.calls))
        self.assertEqual([c[:3] for c in fake.calls][-1], ("shell", "pm", "enable"))

    def test_iisu_is_re_enabled_even_when_the_push_fails(self):
        fake = FakeAdb(existing=("media",), push_ok=False)
        with tempfile.TemporaryDirectory() as tmp, self._patched(fake)[0], self._patched(fake)[1]:
            archive = Path(tmp) / "b.zip"
            svc.create_backup(archive)
            fake.calls.clear()
            svc.restore_backup(archive)
        self.assertEqual([c[:3] for c in fake.calls][-1], ("shell", "pm", "enable"))

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


class ChunkTests(unittest.TestCase):
    def _folder(self, tmp, sizes):
        root = Path(tmp) / "f"
        for name, size in sizes.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
        return root

    def test_files_are_grouped_up_to_the_chunk_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._folder(tmp, {"a": 40, "b": 40, "c": 40, "d": 40})
            chunks = svc.plan_chunks(root, chunk_bytes=100)
            self.assertEqual([[p.name for p in c] for c in chunks], [["a", "b"], ["c", "d"]])

    def test_an_oversized_file_gets_a_chunk_of_its_own_and_nothing_is_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._folder(tmp, {"a": 10, "big": 500, "c": 10})
            chunks = svc.plan_chunks(root, chunk_bytes=100)
            self.assertEqual(sorted(p.name for c in chunks for p in c), ["a", "big", "c"])
            self.assertIn(["big"], [[p.name for p in c] for c in chunks])

    def test_empty_folder_has_no_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(svc.plan_chunks(Path(tmp)), [])

    def test_tar_paths_are_relative_to_the_folder(self):
        import tarfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self._folder(tmp, {"iiSULauncher/art/a.png": 5})
            tar_path = Path(tmp) / "t.tar"
            svc.build_restore_tar(root, [root / "iiSULauncher" / "art" / "a.png"], tar_path)
            with tarfile.open(tar_path) as tar:
                self.assertEqual(tar.getnames(), ["iiSULauncher/art/a.png"])


class FlakyLinkTests(unittest.TestCase):
    """The link dropping mid-push (seen live) must cost a retry, not the restore."""

    def test_a_dropped_push_is_retried_after_wait_for_device_and_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "f"
            (folder / "iiSULauncher").mkdir(parents=True)
            (folder / "iiSULauncher" / "a.png").write_bytes(b"x" * 10)
            calls, state = [], {"pushes": 0}

            def fake(*args, timeout=30):
                calls.append(args)
                if args[0] == "shell" and args[1].startswith("df -h"):
                    return _completed(stdout=FakeAdb.DF_HEALTHY)
                if args[0] == "push":
                    state["pushes"] += 1
                    return _completed(1, stderr="adb: error: failed to read copy response: EOF\n") if state["pushes"] < 3 else _completed()
                return _completed()

            with patch.object(svc, "adb_command", fake):
                result = svc._restore_folder("media", folder, Path(tmp))
            self.assertEqual(result, "restored")
            self.assertEqual(state["pushes"], 3)
            self.assertEqual(sum(1 for c in calls if c[0] == "wait-for-device"), 2)

    def test_giving_up_names_the_part_and_the_real_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "f"
            (folder / "iiSULauncher").mkdir(parents=True)
            (folder / "iiSULauncher" / "a.png").write_bytes(b"x" * 10)

            def fake(*args, timeout=30):
                if args[0] == "shell" and args[1].startswith("df -h"):
                    return _completed(stdout=FakeAdb.DF_HEALTHY)
                if args[0] == "push":
                    return _completed(1, stderr="adb: error: device offline\n")
                return _completed()

            with patch.object(svc, "adb_command", fake):
                result = svc._restore_folder("media", folder, Path(tmp))
            self.assertTrue(result.startswith("failed: part 1 of 1"))
            self.assertIn("device offline", result)

    def test_big_libraries_are_sent_in_several_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "f"
            folder.mkdir()
            for i in range(5):
                (folder / f"f{i}.bin").write_bytes(b"x" * 60)
            pushes = []

            def fake(*args, timeout=30):
                if args[0] == "shell" and args[1].startswith("df -h"):
                    return _completed(stdout=FakeAdb.DF_HEALTHY)
                if args[0] == "push":
                    pushes.append(args[2])
                return _completed()

            with patch.object(svc, "adb_command", fake):
                result = svc._restore_folder("media", folder, Path(tmp), chunk_bytes=130)
            self.assertEqual(result, "restored")
            self.assertEqual(len(pushes), 3)  # 2 + 2 + 1 files


class ErrorLineTests(unittest.TestCase):
    def test_prefers_the_error_over_the_trailing_progress_line(self):
        stderr = "adb: error: failed to copy 'x' to '/sdcard/y': remote secure_mkdirs failed\n[  0%] /sdcard/y/gb.png: 100%\n"
        self.assertIn("secure_mkdirs failed", svc.error_line(stderr))

    def test_falls_back_to_the_last_line_then_to_a_default(self):
        self.assertEqual(svc.error_line("one\ntwo\n"), "two")
        self.assertEqual(svc.error_line("", ""), "adb push failed")

    def test_looks_at_stdout_too_and_truncates(self):
        self.assertTrue(svc.error_line("", "x" * 400 + " error").endswith("error") or len(svc.error_line("", "x" * 400)) == 160)


class SummaryTests(unittest.TestCase):
    def test_summary(self):
        result = svc.BackupResult(backed_up={"media": 3}, skipped={"data": "denied"})
        self.assertEqual(result.total_files, 3)
        self.assertEqual(result.summary(), "media: 3 file(s); data: skipped (denied)")


if __name__ == "__main__":
    unittest.main()
