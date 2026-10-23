"""Unit tests for shared/vm_storage.py."""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared import vm_storage as vs  # noqa: E402; path set up above

DF_FULL = """Filesystem       Size Used Avail Use% Mounted on
/dev/block/dm-55 5.8G 5.6G     0 100% /data/user/0
"""
DF_HEALTHY = """Filesystem       Size Used Avail Use% Mounted on
/dev/block/dm-55  16G 4.0G  12G  25% /data
"""


class ParseTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(vs.parse_size_kb("1G"), 1024 * 1024)
        self.assertEqual(vs.parse_size_kb("50M"), 50 * 1024)
        self.assertEqual(vs.parse_size_kb("123K"), 123)
        self.assertEqual(vs.parse_size_kb("0"), 0)
        self.assertEqual(vs.parse_size_kb("5.8G"), int(5.8 * 1024 * 1024))
        self.assertIsNone(vs.parse_size_kb("lots"))

    def test_full_disk_from_the_real_output_shape(self):
        usage = vs.parse_df(DF_FULL)
        self.assertEqual(usage.avail_kb, 0)
        self.assertEqual(usage.used_percent, round(100 * usage.used_kb / usage.total_kb))
        self.assertGreater(usage.used_percent, 95)

    def test_healthy_disk(self):
        usage = vs.parse_df(DF_HEALTHY)
        self.assertEqual(vs.usage_level(usage), "ok")

    def test_garbage_and_errors(self):
        self.assertIsNone(vs.parse_df(""))
        self.assertIsNone(vs.parse_df("df: '/data': No such file or directory"))
        self.assertIsNone(vs.parse_df("Filesystem Size Used Avail Use% Mounted on"))


class LevelTests(unittest.TestCase):
    def test_critical_when_nearly_nothing_is_free(self):
        self.assertEqual(vs.usage_level(vs.parse_df(DF_FULL)), "critical")
        self.assertEqual(vs.usage_level(vs.StorageUsage(1000 * 1024, 900 * 1024, 100 * 1024)), "critical")

    def test_low_under_ten_percent_free(self):
        self.assertEqual(vs.usage_level(vs.StorageUsage(10 * 1024 * 1024, 9 * 1024 * 1024 + 300 * 1024, 700 * 1024)), "low")

    def test_ok(self):
        self.assertEqual(vs.usage_level(vs.StorageUsage(10 * 1024 * 1024, 5 * 1024 * 1024, 5 * 1024 * 1024)), "ok")

    def test_describe(self):
        text = vs.describe(vs.StorageUsage(6 * 1024 * 1024, 3 * 1024 * 1024, 3 * 1024 * 1024))
        self.assertEqual(text, "3.0 GB used of 6.0 GB (50%), 3.0 GB free")


class ConfigIniTests(unittest.TestCase):
    def _ini(self, tmp, text):
        path = Path(tmp) / "config.ini"
        path.write_text(text, encoding="utf-8")
        return path

    def test_replaces_the_existing_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ini(tmp, "hw.ramSize=2048\ndisk.dataPartition.size=6G\nhw.gpu.mode=auto\n")
            self.assertTrue(vs.set_data_partition_size(path, 16))
            self.assertEqual(path.read_text(encoding="utf-8"), "hw.ramSize=2048\ndisk.dataPartition.size=16G\nhw.gpu.mode=auto\n")

    def test_adds_it_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ini(tmp, "hw.ramSize=2048\n")
            self.assertTrue(vs.set_data_partition_size(path))
            self.assertIn("disk.dataPartition.size=16G", path.read_text(encoding="utf-8"))

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ini(tmp, "disk.dataPartition.size=16G\n")
            self.assertFalse(vs.set_data_partition_size(path, 16))

    def test_missing_file_is_a_no_op(self):
        self.assertFalse(vs.set_data_partition_size(Path("Z:/nope/config.ini")))

    def test_reads_back_the_configured_size(self):
        self.assertEqual(vs.parse_configured_gb("a=b\ndisk.dataPartition.size=6G\n"), 6.0)
        self.assertEqual(vs.parse_configured_gb("disk.dataPartition.size=512M\n"), 0.5)
        self.assertIsNone(vs.parse_configured_gb("hw.ramSize=2048\n"))


class ParseDfKbTests(unittest.TestCase):
    def test_reads_exact_kilobytes(self):
        out = "Filesystem       1K-blocks    Used Available Use% Mounted on\n/dev/block/dm-55   8107484 1257732   6702296  16% /data/user/0\n"
        usage = vs.parse_df_kb(out)
        self.assertEqual((usage.total_kb, usage.used_kb, usage.avail_kb), (8107484, 1257732, 6702296))

    def test_garbage_is_none(self):
        self.assertIsNone(vs.parse_df_kb("df: /data: No such file"))
        self.assertIsNone(vs.parse_df_kb(DF_HEALTHY))  # human-readable sizes, not 1K blocks


class ClampTests(unittest.TestCase):
    def test_values_inside_the_range_pass_through(self):
        self.assertEqual(vs.clamp_storage_gb(32), 32)
        self.assertEqual(vs.clamp_storage_gb("24"), 24)

    def test_out_of_range_values_are_pulled_in(self):
        self.assertEqual(vs.clamp_storage_gb(1), vs.MIN_DATA_PARTITION_GB)
        self.assertEqual(vs.clamp_storage_gb(9999), vs.MAX_DATA_PARTITION_GB)

    def test_garbage_falls_back_to_the_default(self):
        self.assertEqual(vs.clamp_storage_gb(None), vs.DEFAULT_DATA_PARTITION_GB)
        self.assertEqual(vs.clamp_storage_gb("lots"), vs.DEFAULT_DATA_PARTITION_GB)


class ResetAvdDataTests(unittest.TestCase):
    def _avd(self, tmp):
        avd = Path(tmp) / "x.avd"
        (avd / "snapshots").mkdir(parents=True)
        for name in ("userdata-qemu.img.qcow2", "encryptionkey.img.qcow2", "version_num.cache", "config.ini", "hardware-qemu.ini"):
            (avd / name).write_text("x")
        return avd

    def test_removes_data_files_and_keeps_the_avd_definition(self):
        from bridge import portable_sdk

        with tempfile.TemporaryDirectory() as tmp:
            avd = self._avd(tmp)
            handled = portable_sdk.reset_avd_data(avd)
            self.assertIn("userdata-qemu.img.qcow2", handled)
            self.assertIn("version_num.cache", handled)
            self.assertFalse((avd / "userdata-qemu.img.qcow2").exists())
            self.assertFalse((avd / "snapshots").exists())
            self.assertTrue((avd / "config.ini").exists())

    def test_aside_moves_instead_of_deleting(self):
        from bridge import portable_sdk

        with tempfile.TemporaryDirectory() as tmp:
            avd = self._avd(tmp)
            aside = Path(tmp) / "old"
            portable_sdk.reset_avd_data(avd, aside=aside)
            self.assertTrue((aside / "userdata-qemu.img.qcow2").exists())
            self.assertFalse((avd / "userdata-qemu.img.qcow2").exists())


if __name__ == "__main__":
    unittest.main()
