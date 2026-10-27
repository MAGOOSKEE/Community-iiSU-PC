"""
Smoke test for the Manager window: builds every page offscreen and switches
through every nav entry and sub-page, so a page that fails to construct or a
nav entry with no page registered fails here instead of on someone's machine.
Runs against a temporary config.json, never the real one. Skipped when PySide6
is not installed. No event loop runs, so no timer, adb call or update check
fires.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import PySide6  # noqa: F401
except ImportError:  # pragma: no cover; depends on the environment
    PySide6 = None


@unittest.skipIf(PySide6 is None, "PySide6 is not installed")
class ManagerWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import bridge.ui  # noqa: F401; puts bridge/ and installer/ on sys.path
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import bridge_config
        import bridge.ui.main_window as main_window
        import bridge.ui.pages.home as home

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "config.json"
        roms = Path(self._tmp.name) / "roms"
        roms.mkdir()
        self.config = {
            "avd_name": "iisuwin", "roms_dir": str(roms), "bridge_port": 7737, "emulators": {},
            "display": {"width": 1080, "height": 1920, "density": 420}, "vm_backend": "avd",
        }

        self.main_window = main_window
        self._patched = [(module, "CONFIG_PATH", module.CONFIG_PATH) for module in (bridge_config, main_window, home)]
        for module, name, _ in self._patched:
            setattr(module, name, self.config_path)
        self._side_effects = main_window.ManagerWindow._trigger_page_side_effects
        main_window.ManagerWindow._trigger_page_side_effects = lambda window, key: None

        def restore():
            for module, name, original in self._patched:
                setattr(module, name, original)
            main_window.ManagerWindow._trigger_page_side_effects = self._side_effects

        self.addCleanup(restore)

    def _window(self):
        window = self.main_window.ManagerWindow()

        def close():
            window._status_timer.stop()
            window._dirty_timer.stop()
            window.close()
            window.deleteLater()

        self.addCleanup(close)
        return window

    def test_every_page_builds_and_shows_when_configured(self):
        from bridge.ui.sidebar import DANGER_NAV_ITEMS, NAV_GROUPS, NAV_ITEMS

        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        self.assertTrue(window.configured)
        for key, _icon, _label in NAV_ITEMS + DANGER_NAV_ITEMS:
            window.show_page(key)
            self.assertEqual(window.current_page, key)
            self.assertIs(window.page_stack.currentWidget(), window.pages[key])
            for sub_key, _sub_label in NAV_GROUPS.get(key, []):
                window._show_subpage(key, sub_key)
                self.assertEqual(window.current_subpage, sub_key)
                self.assertIs(window.subpage_stacks[key].currentWidget(), window.subpages[sub_key])

    def test_every_nav_group_entry_has_a_page(self):
        from bridge.ui.sidebar import NAV_GROUPS

        window = self._window()
        for subs in NAV_GROUPS.values():
            for sub_key, _label in subs:
                self.assertIn(sub_key, window.subpages)

    def test_an_install_without_a_config_still_opens_and_locks_the_settings(self):
        from bridge.ui.sidebar import LOCKED_NAV

        self.assertFalse(self.config_path.exists())
        window = self._window()
        self.assertFalse(window.configured)
        for key in LOCKED_NAV:
            window._on_nav_click(key)
            self.assertEqual(window.current_page, "home")


if __name__ == "__main__":
    unittest.main()
