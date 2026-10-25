"""Unit tests for bridge/services/vm_storage_service.py (no VM needed)."""

import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import vm_storage_service as svc  # noqa: E402; path set up above

GB = 1024 * 1024


def done(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


DISKSTATS = (
    'Package Names: ["com.iisulauncher","com.example.game"]\n'
    "App Sizes: [1048576,1048576]\nApp Data Sizes: [2097152,1048576]\nCache Sizes: [0,0]\n"
)


class FakeAdb:
    def __init__(self, state="device", job_listed=True, fresh_after=1):
        self.state = state
        self.job_listed = job_listed
        self.fresh_after = fresh_after  # diskstats calls before the forced job shows new numbers
        self.diskstats_calls = 0
        self.log = []

    def __call__(self, *args, timeout=30):
        command = " ".join(args)
        self.log.append(command)
        if command == "get-state":
            return done(self.state + "\n")
        if command == "shell df -k /data":
            return done("Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/x 8107484 1257732 6702296 16% /data\n")
        if command.startswith("shell du -sk"):
            return done(f"{2 * GB}\t/sdcard/Android/media/com.iisulauncher\n1000\t/sdcard/Roms\n{3 * GB}\t/sdcard\n")
        if command == "shell dumpsys diskstats":
            self.diskstats_calls += 1
            return done("Data-Free: 1K / 2K total\n" if self.diskstats_calls <= self.fresh_after else DISKSTATS)
        if "grep -m1 DiskStatsLoggingService" in command:
            return done("  JOB #1000/1145656139: c96867a android/com.android.server.storage.DiskStatsLoggingService\n" if self.job_listed else "")
        return done()


class CollectBreakdownTests(unittest.TestCase):
    def _collect(self, adb):
        ticks = iter(range(0, 100_000, 5))
        return svc.collect_breakdown(adb, sleep=lambda _s: None, clock=lambda: next(ticks))

    def test_builds_the_categories_from_df_du_and_diskstats(self):
        breakdown = self._collect(FakeAdb())
        self.assertTrue(breakdown.itemised)
        self.assertEqual(breakdown.segment("iisu_art").kb, 2 * GB)
        self.assertEqual(breakdown.segment("roms").kb, 1000)
        self.assertGreater(breakdown.segment("iisu_app").kb, 0)
        self.assertEqual(breakdown.segment("free").kb, 6702296)

    def test_forces_the_disk_stats_job_by_the_id_it_finds(self):
        adb = FakeAdb()
        self._collect(adb)
        self.assertIn("shell cmd jobscheduler run -f android 1145656139", adb.log)

    def test_without_the_job_it_still_returns_a_breakdown_without_per_app_sizes(self):
        breakdown = self._collect(FakeAdb(job_listed=False))
        self.assertFalse(breakdown.itemised)
        self.assertEqual(breakdown.segment("iisu_art").kb, 2 * GB)

    def test_a_stopped_vm_is_a_clear_error(self):
        with self.assertRaises(svc.StorageError) as caught:
            self._collect(FakeAdb(state="offline"))
        self.assertIn("isn't running", str(caught.exception))


class OldCopyTests(unittest.TestCase):
    def test_finds_only_the_leftover_folders_and_sizes_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "iisuwin.avd").mkdir()
            (home / "iisuwin.pre-resize-20260101-120000").mkdir()
            (home / "iisuwin.pre-resize-20260101-120000" / "a.bin").write_bytes(b"x" * 100)
            (home / "iisuwin.avd.bak-preswap").mkdir()
            (home / "other.avd").mkdir()
            (home / "iisuwin.ini").write_text("x")
            copies = svc.find_old_copies("iisuwin", home)
            self.assertEqual([c.path.name for c in copies], ["iisuwin.avd.bak-preswap", "iisuwin.pre-resize-20260101-120000"])
            self.assertEqual(sum(c.size_bytes for c in copies), 100)

    def test_delete_refuses_the_live_vm_and_foreign_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            live = home / "iisuwin.avd"
            live.mkdir()
            with self.assertRaises(svc.StorageError):
                svc.delete_old_copy(live, "iisuwin", home)
            elsewhere = Path(tmp) / "elsewhere" / "iisuwin.pre-resize-1"
            elsewhere.mkdir(parents=True)
            with self.assertRaises(svc.StorageError):
                svc.delete_old_copy(elsewhere, "iisuwin", home)
            self.assertTrue(live.is_dir() and elsewhere.is_dir())

    def test_delete_removes_read_only_files_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / "iisuwin.pre-resize-1" / "data" / "misc"
            old.mkdir(parents=True)
            stuck = old / "pstore.bin"
            stuck.write_bytes(b"x")
            os.chmod(stuck, stat.S_IREAD)
            svc.delete_old_copy(home / "iisuwin.pre-resize-1", "iisuwin", home)
            self.assertFalse((home / "iisuwin.pre-resize-1").exists())

    def test_delete_removes_an_old_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / "iisuwin.pre-resize-1"
            old.mkdir()
            svc.delete_old_copy(old, "iisuwin", home)
            self.assertFalse(old.exists())


def make_avd(tmp, size="16G"):
    home = Path(tmp) / "avd-home"
    avd = home / "x.avd"
    (avd / "snapshots").mkdir(parents=True)
    (avd / "config.ini").write_text(f"hw.ramSize=2048\ndisk.dataPartition.size={size}\n")
    for name in ("userdata-qemu.img.qcow2", "userdata-qemu.img", "encryptionkey.img.qcow2", "version_num.cache"):
        (avd / name).write_text("old-" + name)
    (avd / "hardware-qemu.ini").write_text("keep me")
    return avd


class FakeSteps:
    def __init__(self, avd, fail_install=False, bad_backup=False, restore_raises=False):
        self.avd = avd
        self.fail_install = fail_install
        self.bad_backup = bad_backup
        self.restore_raises = restore_raises
        self.calls = []

    def steps(self):
        return svc.ResizeSteps(self.backup, self.verify, self.stop, self.install, self.restore)

    def backup(self, path):
        self.calls.append("backup")
        path.write_text("zip")

    def verify(self, path):
        self.calls.append("verify")
        if self.bad_backup:
            raise ValueError("empty archive")

    def stop(self):
        self.calls.append("stop")

    def install(self, hook):
        self.calls.append("install")
        # the fresh VM's files appear when it boots
        (self.avd / "userdata-qemu.img.qcow2").write_text("NEW")
        if self.fail_install:
            raise RuntimeError("emulator crashed")
        hook()

    def restore(self, path):
        self.calls.append("restore")
        if self.restore_raises:
            raise RuntimeError("adb push failed")
        return {"media": "restored"}


class ResizeTests(unittest.TestCase):
    NOW = lambda self: datetime(2026, 1, 2, 3, 4, 5)  # noqa: E731

    def _run(self, tmp, fake, new_gb=32):
        return svc.resize(fake.avd, new_gb, Path(tmp) / "backup.zip", fake.steps(), now=self.NOW)

    def test_happy_path_order_new_size_and_old_data_kept_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            avd = make_avd(tmp)
            fake = FakeSteps(avd)
            result = self._run(tmp, fake, 32)
            self.assertEqual(fake.calls, ["backup", "verify", "stop", "install", "restore"])
            self.assertIn("disk.dataPartition.size=32G", (avd / "config.ini").read_text())
            self.assertEqual((avd / "userdata-qemu.img.qcow2").read_text(), "NEW")
            self.assertFalse((avd / "version_num.cache").exists())  # no stale cache next to a new image
            self.assertTrue((avd / "hardware-qemu.ini").exists())
            self.assertEqual(result.old_copy.name, "x.pre-resize-20260102-030405")
            self.assertEqual((result.old_copy / "userdata-qemu.img.qcow2").read_text(), "old-userdata-qemu.img.qcow2")
            self.assertIn("disk.dataPartition.size=16G", (result.old_copy / "config.ini").read_text())
            self.assertEqual(result.restore_outcome, {"media": "restored"})

    def test_an_unverifiable_backup_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            avd = make_avd(tmp)
            fake = FakeSteps(avd, bad_backup=True)
            with self.assertRaises(svc.ResizeError):
                self._run(tmp, fake)
            self.assertEqual(fake.calls, ["backup", "verify"])  # the VM was never stopped
            self.assertEqual((avd / "userdata-qemu.img.qcow2").read_text(), "old-userdata-qemu.img.qcow2")
            self.assertIn("16G", (avd / "config.ini").read_text())
            self.assertEqual([p.name for p in avd.parent.iterdir()], ["x.avd"])

    def test_a_failed_install_puts_the_old_vm_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            avd = make_avd(tmp)
            fake = FakeSteps(avd, fail_install=True)
            with self.assertRaises(svc.ResizeError) as caught:
                self._run(tmp, fake)
            self.assertIn("old one was put back", str(caught.exception))
            for name in ("userdata-qemu.img.qcow2", "userdata-qemu.img", "encryptionkey.img.qcow2", "version_num.cache"):
                self.assertEqual((avd / name).read_text(), "old-" + name)
            self.assertIn("disk.dataPartition.size=16G", (avd / "config.ini").read_text())
            self.assertEqual([p.name for p in avd.parent.iterdir()], ["x.avd"])  # nothing left behind
            self.assertEqual(fake.calls.count("stop"), 2)  # once to begin, once to clear a half-built VM

    def test_a_failed_restore_keeps_the_new_vm_and_reports_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            avd = make_avd(tmp)
            result = self._run(tmp, FakeSteps(avd, restore_raises=True))
            self.assertEqual((avd / "userdata-qemu.img.qcow2").read_text(), "NEW")
            self.assertIn("adb push failed", result.restore_error)
            self.assertTrue(result.old_copy.is_dir())

    def test_the_size_is_clamped_to_the_supported_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            avd = make_avd(tmp)
            self._run(tmp, FakeSteps(avd), new_gb=9999)
            self.assertIn("disk.dataPartition.size=128G", (avd / "config.ini").read_text())

    def test_a_folder_that_is_not_an_avd_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "nothing.avd"
            empty.mkdir()
            with self.assertRaises(svc.ResizeError):
                self._run(tmp, FakeSteps(empty))


class HostInfoTests(unittest.TestCase):
    def test_reports_the_file_size_configured_size_and_old_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            avd = make_avd(tmp, "24G")
            (avd / svc.DATA_IMAGE).write_bytes(b"x" * 500)
            (avd.parent / "x.pre-resize-1").mkdir()
            info = svc.host_info("x", avd.parent)
            self.assertEqual(info.data_bytes, 500)
            self.assertEqual(info.configured_gb, 24.0)
            self.assertEqual(len(info.old_copies), 1)

    def test_a_missing_vm_is_zeros_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = svc.host_info("nope", Path(tmp))
            self.assertEqual((info.data_bytes, info.configured_gb, info.old_copies), (0, None, []))


if __name__ == "__main__":
    unittest.main()
