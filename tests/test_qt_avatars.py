"""
Unit tests for the avatar cache in shared/qt_avatars.py: a fresh cached avatar
is used as is, a stale or missing one is refetched, a failed fetch never
overwrites what is cached. No network: fetch_avatar_bytes is patched. Skipped
when PySide6 is not installed (the module imports it).
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from shared import qt_avatars
except ImportError:  # pragma: no cover; depends on the environment
    qt_avatars = None


@unittest.skipIf(qt_avatars is None, "PySide6 is not installed")
class AvatarCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / "avatars"

    def _write(self, username: str, data: bytes, age_seconds: float) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        path = self.cache / f"{username}.avatar"
        path.write_bytes(data)
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))

    def test_nothing_cached(self):
        self.assertEqual(qt_avatars.cached_avatar("someone", self.cache), (None, False))

    def test_a_recent_avatar_is_fresh(self):
        self._write("someone", b"img", age_seconds=60)
        self.assertEqual(qt_avatars.cached_avatar("someone", self.cache), (b"img", True))

    def test_an_old_avatar_is_returned_but_stale(self):
        self._write("someone", b"img", age_seconds=qt_avatars.AVATAR_MAX_AGE_SECONDS + 60)
        self.assertEqual(qt_avatars.cached_avatar("someone", self.cache), (b"img", False))

    def test_a_successful_fetch_is_cached(self):
        with patch.object(qt_avatars, "fetch_avatar_bytes", return_value=b"new"):
            self.assertEqual(qt_avatars.fetch_and_cache_avatar("someone", self.cache), b"new")
        self.assertEqual(qt_avatars.cached_avatar("someone", self.cache), (b"new", True))

    def test_a_failed_fetch_keeps_the_old_copy(self):
        self._write("someone", b"old", age_seconds=qt_avatars.AVATAR_MAX_AGE_SECONDS + 60)
        with patch.object(qt_avatars, "fetch_avatar_bytes", return_value=None):
            self.assertIsNone(qt_avatars.fetch_and_cache_avatar("someone", self.cache))
        self.assertEqual(qt_avatars.cached_avatar("someone", self.cache)[0], b"old")

    def test_an_unwritable_cache_is_ignored(self):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(b"a file where the folder should be")
        with patch.object(qt_avatars, "fetch_avatar_bytes", return_value=b"new"):
            self.assertEqual(qt_avatars.fetch_and_cache_avatar("someone", self.cache), b"new")


if __name__ == "__main__":
    unittest.main()
