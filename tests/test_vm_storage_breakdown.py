"""Unit tests for shared/vm_storage_breakdown.py."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import vm_storage_breakdown as bd  # noqa: E402; path set up above
from shared.vm_storage import StorageUsage  # noqa: E402

GB = 1024 * 1024  # in KB

DISKSTATS = """Latency: 0ms [512B Data Write]
Data-Free: 6702296K / 8107484K total = 82% free
App Size: 332480512
Package Names: ["com.iisulauncher","com.google.android.gms","com.android.chrome","com.example.game","com.android.vending"]
App Sizes: [104857600,209715200,104857600,52428800,52428800]
App Data Sizes: [2147483648,104857600,52428800,10485760,1048576]
Cache Sizes: [1048576,1048576,0,0,0]
"""


class ParseDiskstatsTests(unittest.TestCase):
    def test_reads_every_package(self):
        packages = bd.parse_diskstats(DISKSTATS)
        self.assertEqual(set(packages), {"com.iisulauncher", "com.google.android.gms", "com.android.chrome", "com.example.game", "com.android.vending"})
        iisu = packages["com.iisulauncher"]
        self.assertEqual((iisu.app_bytes, iisu.data_bytes, iisu.cache_bytes), (104857600, 2147483648, 1048576))

    def test_a_dump_without_the_per_app_section_is_none(self):
        self.assertIsNone(bd.parse_diskstats("Data-Free: 1K / 2K total = 50% free\nFile-based Encryption: true\n"))

    def test_malformed_arrays_are_none(self):
        self.assertIsNone(bd.parse_diskstats(DISKSTATS.replace('["com.iisulauncher"', "[oops")))

    def test_short_size_arrays_do_not_crash(self):
        text = DISKSTATS.replace("Cache Sizes: [1048576,1048576,0,0,0]", "Cache Sizes: [1]")
        self.assertEqual(bd.parse_diskstats(text)["com.android.vending"].cache_bytes, 0)


class ParseDuTests(unittest.TestCase):
    def test_reads_sizes_and_skips_errors(self):
        out = "1200\t/sdcard/Roms\ndu: /sdcard/nope: No such file or directory\n340000\t/sdcard\n"
        self.assertEqual(bd.parse_du(out), {"/sdcard/Roms": 1200, "/sdcard": 340000})


class BuildBreakdownTests(unittest.TestCase):
    USAGE = StorageUsage(total_kb=16 * GB, used_kb=6 * GB, avail_kb=10 * GB)

    def _build(self, packages=None, **shared):
        shared.setdefault("sdcard", 0)
        return bd.build_breakdown(self.USAGE, packages if packages is not None else bd.parse_diskstats(DISKSTATS), shared)

    def test_art_roms_and_other_files_come_from_shared_storage(self):
        b = self._build(iisu_media=2 * GB, roms=100 * 1024, sdcard=3 * GB)
        self.assertEqual(b.segment("iisu_art").kb, 2 * GB)
        self.assertEqual(b.segment("roms").kb, 100 * 1024)
        self.assertEqual(b.segment("files").kb, 3 * GB - 2 * GB - 100 * 1024)

    def test_packages_are_split_into_iisu_google_and_other(self):
        b = self._build()
        self.assertEqual(b.segment("iisu_app").kb, (104857600 + 2147483648 + 1048576) // 1024)
        google = (209715200 + 104857600 + 1048576) + (104857600 + 52428800) + (52428800 + 1048576)
        self.assertEqual(b.segment("google").kb, google // 1024)
        self.assertEqual(b.segment("apps").kb, (52428800 + 10485760) // 1024)

    def test_system_is_whatever_is_used_but_unaccounted_for(self):
        b = self._build(iisu_media=GB, sdcard=GB)
        known = sum(b.segment(k).kb for k in ("iisu_art", "iisu_app", "roms", "google", "apps", "files"))
        self.assertEqual(b.segment("system").kb, self.USAGE.used_kb - known)

    def test_free_space_is_the_available_amount(self):
        self.assertEqual(self._build().segment("free").kb, 10 * GB)

    def test_overcounting_never_makes_system_negative(self):
        b = self._build(iisu_media=20 * GB, sdcard=20 * GB)
        self.assertEqual(b.segment("system").kb, 0)

    def test_without_per_app_sizes_the_system_bucket_says_so(self):
        b = bd.build_breakdown(self.USAGE, None, {"sdcard": 0})
        self.assertFalse(b.itemised)
        self.assertEqual(b.segment("system").label, "Apps and Android system")
        self.assertEqual(b.segment("apps").kb, 0)
        self.assertEqual(b.segment("system").kb, self.USAGE.used_kb)

    def test_top_apps_are_sorted_biggest_first(self):
        top = self._build().top_apps
        self.assertEqual(top[0][0], "com.iisulauncher")
        self.assertEqual([kb for _n, kb in top], sorted((kb for _n, kb in top), reverse=True))

    def test_segments_keep_the_category_order_and_distinct_colors(self):
        b = self._build()
        self.assertEqual([s.key for s in b.segments], [key for key, _l, _c in bd.CATEGORIES])
        self.assertEqual(len({s.color for s in b.segments}), len(b.segments))


class FormattingTests(unittest.TestCase):
    def test_format_kb(self):
        self.assertEqual(bd.format_kb(512), "512 KB")
        self.assertEqual(bd.format_kb(2048), "2 MB")
        self.assertEqual(bd.format_kb(3 * GB), "3.0 GB")

    def test_bar_fractions_sum_to_one(self):
        b = bd.build_breakdown(StorageUsage(16 * GB, 6 * GB, 10 * GB), bd.parse_diskstats(DISKSTATS), {"sdcard": 0})
        self.assertAlmostEqual(sum(f for _s, f in bd.bar_fractions(b.segments)), 1.0)

    def test_empty_bar_does_not_divide_by_zero(self):
        self.assertTrue(all(f == 0 for _s, f in bd.bar_fractions([bd.Segment("free", "Free", "#000", 0)])))


if __name__ == "__main__":
    unittest.main()
