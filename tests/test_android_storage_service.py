"""Unit tests for bridge/services/android_storage_service.py's pure logic
(path helpers, quoting, size formatting). The ADB-calling functions need a
real device and are exercised manually, same as the rest of this page's
original tkinter implementation was."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import android_storage_service as svc


class PathHelperTests(unittest.TestCase):
    def test_android_join_from_root(self):
        self.assertEqual(svc.android_join("/", "foo"), "/foo")

    def test_android_join_normal(self):
        self.assertEqual(svc.android_join("/storage/emulated/0", "Download"), "/storage/emulated/0/Download")

    def test_android_join_trailing_slash(self):
        self.assertEqual(svc.android_join("/storage/emulated/0/", "Download"), "/storage/emulated/0/Download")

    def test_android_parent_of_root_is_root(self):
        self.assertEqual(svc.android_parent("/"), "/")
        self.assertEqual(svc.android_parent(""), "/")

    def test_android_parent_normal(self):
        self.assertEqual(svc.android_parent("/storage/emulated/0/Download"), "/storage/emulated/0")

    def test_android_parent_trailing_slash(self):
        self.assertEqual(svc.android_parent("/storage/emulated/0/Download/"), "/storage/emulated/0")

    def test_android_parent_top_level(self):
        self.assertEqual(svc.android_parent("/storage"), "/")


class QuoteTests(unittest.TestCase):
    def test_quotes_plain_value(self):
        self.assertEqual(svc.android_remote_quote("hello"), "'hello'")

    def test_escapes_embedded_single_quote(self):
        self.assertEqual(svc.android_remote_quote("it's"), "'it'\\''s'")


class FormatSizeTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(svc.format_size(500), "500 B")

    def test_kilobytes(self):
        self.assertEqual(svc.format_size(2048), "2.0 KB")

    def test_megabytes(self):
        self.assertEqual(svc.format_size(5 * 1024 * 1024), "5.0 MB")

    def test_gigabytes(self):
        self.assertEqual(svc.format_size(3 * 1024 ** 3), "3.0 GB")


if __name__ == "__main__":
    unittest.main()
