"""Unit tests for bridge/create_shortcut.py's icon choice: iiSU's own icon or
the iiPC one, for the shortcuts and the Manager. No shortcut is written:
PowerShell, the desktop folder and icon extraction are stubbed out."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import create_shortcut  # noqa: E402; path set up above


class IconChoiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "config.json"
        self.extracted = self.tmp / ".iisu_icon.ico"
        for name, value in (("CONFIG_PATH", self.config), ("EXTRACTED_ICON_PATH", self.extracted)):
            patcher = patch.object(create_shortcut, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_choice_is_read_from_the_config(self):
        self.assertEqual(create_shortcut.configured_icon_choice(), create_shortcut.ICON_IISU, "no config yet")
        self.config.write_text(json.dumps({"icon_choice": "iipc"}), encoding="utf-8")
        self.assertEqual(create_shortcut.configured_icon_choice(), create_shortcut.ICON_IIPC)
        self.config.write_text("not json", encoding="utf-8")
        self.assertEqual(create_shortcut.configured_icon_choice(), create_shortcut.ICON_IISU)

    def test_iisus_icon_is_used_only_once_extracted(self):
        self.assertEqual(create_shortcut.icon_path_for(create_shortcut.ICON_IISU), create_shortcut.FALLBACK_ICON_PATH)
        self.extracted.write_bytes(b"ico")
        self.assertEqual(create_shortcut.icon_path_for(create_shortcut.ICON_IISU), self.extracted)
        self.assertEqual(create_shortcut.icon_path_for(create_shortcut.ICON_IIPC), create_shortcut.FALLBACK_ICON_PATH)

    def _create(self, icon_choice):
        with patch.object(create_shortcut, "extract_iisu_icon", return_value=self.extracted) as extracted, \
             patch.object(create_shortcut, "_update_manager_shortcut_icon") as manager, \
             patch.object(create_shortcut, "desktop_dir", return_value=self.tmp), \
             patch.object(create_shortcut, "_write_desktop_file", return_value=self.tmp / "x.desktop") as desktop_file, \
             patch.object(create_shortcut, "_refresh_shell_icon_cache"), \
             patch.object(create_shortcut.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            create_shortcut.create_desktop_shortcut(icon_choice=icon_choice)
        written = run.call_args.args[0][-1] if run.called else desktop_file.call_args.args[2]
        return extracted, manager.call_args.args[0], written

    def test_choosing_iipc_points_both_shortcuts_at_it_and_still_extracts_iisus(self):
        extracted, manager_icon, written = self._create(create_shortcut.ICON_IIPC)
        extracted.assert_called_once()
        self.assertEqual(manager_icon, create_shortcut.FALLBACK_ICON_PATH)
        self.assertIn(str(create_shortcut.FALLBACK_ICON_PATH.resolve() if sys.platform != "win32" else create_shortcut.FALLBACK_ICON_PATH), written)

    def test_choosing_iisu_points_both_shortcuts_at_its_icon(self):
        _extracted, manager_icon, written = self._create(create_shortcut.ICON_IISU)
        self.assertEqual(manager_icon, self.extracted)
        self.assertIn(str(self.extracted.resolve() if sys.platform != "win32" else self.extracted), written)


class NoShortcutTests(unittest.TestCase):
    def test_the_launch_shortcut_goes_and_the_managers_gets_the_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            shortcut = Path(tmp) / create_shortcut.SHORTCUT_NAME
            shortcut.write_text("lnk", encoding="utf-8")
            with patch.object(create_shortcut, "desktop_dir", return_value=Path(tmp)), \
                 patch.object(create_shortcut, "_update_manager_shortcut_icon") as manager, \
                 patch.object(create_shortcut, "_refresh_shell_icon_cache"):
                create_shortcut.remove_desktop_shortcut(create_shortcut.ICON_IIPC)
                create_shortcut.remove_desktop_shortcut(create_shortcut.ICON_IIPC)
            self.assertFalse(shortcut.exists())
        self.assertEqual(manager.call_args.args[0], create_shortcut.FALLBACK_ICON_PATH)


if __name__ == "__main__":
    unittest.main()
