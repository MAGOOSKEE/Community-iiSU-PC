"""
Unit tests for installer/uninstall.py's removal logic. The bug these guard
against actually happened: a single locked file deep inside android-sdk-
portable/ (a lingering emulator/qemu handle) made a bare shutil.rmtree()
abort partway through, leaving a large, silently partial install behind
that only surfaced days later as a confusing, unrelated-looking failure
the next time Setup ran. Pure filesystem logic, no AVD or adb needed.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "installer"))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from uninstall import _remove_tree_best_effort, remove_path  # noqa: E402; path set up above


class RemoveTreeBestEffortTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "tree"
        self.root.mkdir()

    def test_fully_removable_tree_leaves_nothing_locked(self):
        (self.root / "a.txt").write_text("a")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.txt").write_text("b")

        locked = _remove_tree_best_effort(self.root)

        self.assertEqual(locked, [])
        self.assertFalse(self.root.exists())

    def test_locked_file_is_reported_and_siblings_still_removed(self):
        (self.root / "removable.txt").write_text("x")
        locked_file = self.root / "locked.txt"
        locked_file.write_text("x")

        # An open handle blocks deletion on Windows without FILE_SHARE_DELETE,
        # which is what a lingering emulator/qemu process holding a qcow2
        # disk image open looks like in miniature.
        handle = open(locked_file, "rb")
        try:
            locked = _remove_tree_best_effort(self.root)
        finally:
            handle.close()

        # The root itself is reported too, it can't rmdir() while
        # locked.txt is still sitting inside it.
        self.assertEqual(set(locked), {locked_file, self.root})
        self.assertFalse((self.root / "removable.txt").exists())
        self.assertTrue(locked_file.exists())

    def test_locked_file_also_keeps_its_parent_directory(self):
        nested = self.root / "sub"
        nested.mkdir()
        locked_file = nested / "locked.txt"
        locked_file.write_text("x")

        handle = open(locked_file, "rb")
        try:
            locked = _remove_tree_best_effort(self.root)
        finally:
            handle.close()

        self.assertIn(locked_file, locked)
        self.assertIn(nested, locked)
        self.assertTrue(nested.is_dir())


class RemovePathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "tree"
        self.root.mkdir()

    def test_missing_path_reclaims_nothing(self):
        self.assertEqual(remove_path(self.root / "does-not-exist"), 0)

    def test_fully_removable_tree_reclaims_full_size(self):
        (self.root / "a.txt").write_bytes(b"x" * 100)
        reclaimed = remove_path(self.root, attempts=1)
        self.assertEqual(reclaimed, 100)
        self.assertFalse(self.root.exists())

    def test_partially_locked_tree_reclaims_only_what_it_could(self):
        (self.root / "removable.txt").write_bytes(b"x" * 100)
        locked_file = self.root / "locked.txt"
        locked_file.write_bytes(b"x" * 50)

        handle = open(locked_file, "rb")
        try:
            reclaimed = remove_path(self.root, attempts=1, delay=0)
        finally:
            handle.close()

        self.assertEqual(reclaimed, 100)
        self.assertFalse((self.root / "removable.txt").exists())
        self.assertTrue(locked_file.exists())


if __name__ == "__main__":
    unittest.main()
