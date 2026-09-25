"""
Unit tests for bridge/updater.py's _copy_release_tree, the file-copy
step a real, live auto-update applies to every non-git install. Pure
filesystem logic against real temp directories, no network/git/adb
involved.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from updater import _copy_release_tree  # noqa: E402; path set up above


class CopyReleaseTreeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "src"
        self.dst = Path(self._tmp.name) / "dst"
        self.src.mkdir()
        self.dst.mkdir()

    def test_new_files_are_created(self):
        (self.src / "manager.py").write_text("new")
        errors = _copy_release_tree(self.src, self.dst)
        self.assertEqual(errors, [])
        self.assertEqual((self.dst / "manager.py").read_text(), "new")

    def test_existing_files_are_overwritten(self):
        (self.dst / "manager.py").write_text("old")
        (self.src / "manager.py").write_text("new")
        errors = _copy_release_tree(self.src, self.dst)
        self.assertEqual(errors, [])
        self.assertEqual((self.dst / "manager.py").read_text(), "new")

    def test_files_only_in_dst_are_left_alone(self):
        # This is the whole point of the function: config.json, an
        # installed AVD, logs, none of a user's own gitignored state is
        # in the release zip, and none of it should be touched, let
        # alone deleted, by an update.
        (self.dst / "config.json").write_text('{"roms_dir": "D:/Games"}')
        (self.src / "manager.py").write_text("new")
        errors = _copy_release_tree(self.src, self.dst)
        self.assertEqual(errors, [])
        self.assertEqual((self.dst / "config.json").read_text(), '{"roms_dir": "D:/Games"}')
        self.assertTrue((self.dst / "manager.py").is_file())

    def test_nested_directories_are_created_as_needed(self):
        (self.src / "installer" / "smali_patch").mkdir(parents=True)
        (self.src / "installer" / "smali_patch" / "LaunchBridge.smali").write_text("class")
        errors = _copy_release_tree(self.src, self.dst)
        self.assertEqual(errors, [])
        self.assertEqual(
            (self.dst / "installer" / "smali_patch" / "LaunchBridge.smali").read_text(), "class"
        )

    def test_empty_source_directories_are_still_created(self):
        (self.src / "installer" / "input").mkdir(parents=True)
        errors = _copy_release_tree(self.src, self.dst)
        self.assertEqual(errors, [])
        self.assertTrue((self.dst / "installer" / "input").is_dir())


if __name__ == "__main__":
    unittest.main()
