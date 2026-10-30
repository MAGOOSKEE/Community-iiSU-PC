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
import unittest.mock
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

    def test_no_nav_group_holds_a_single_page(self):
        from bridge.ui.sidebar import NAV_GROUPS, NAV_ITEMS

        for key, subs in NAV_GROUPS.items():
            self.assertGreaterEqual(len(subs), 2, f"{key} is a group with one page")
        top_level = [key for key, _icon, _label in NAV_ITEMS]
        self.assertNotIn("emulators", top_level)
        self.assertIn(("emulators", "PC Emulators"), NAV_GROUPS["settings"])

    def test_a_freshly_opened_window_has_nothing_to_save(self):
        # A complete config, as Save would have written it: the minimal fixture lacks
        # defaults every real config has, which alone would read as unsaved edits.
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        complete = self._window()._gather_settings(silent=True)
        self.config_path.write_text(json.dumps(complete), encoding="utf-8")
        window = self._window()
        self.assertFalse(window._is_settings_dirty())

    def test_edits_on_the_merged_fields_reach_the_saved_settings(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        window.advanced_page.avd_name_edit.setText("myavd")
        window.display_page.refresh_edit.setText("120")
        window.display_page.width_edit.setText("1600")
        window.display_page.height_edit.setText("900")
        settings = window._gather_settings(silent=True)
        self.assertEqual(settings["avd_name"], "myavd")
        self.assertEqual(settings["display"]["refresh_rate"], 120)
        self.assertEqual((settings["display"]["width"], settings["display"]["height"]), (1600, 900))
        self.assertTrue(window._is_settings_dirty())

    def test_a_refresh_rate_preset_fills_the_same_field(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        combo = window.display_page.refresh_edit.parent()
        combo.setCurrentIndex(combo.findText("144"))
        self.assertEqual(window.display_page.refresh_edit.text(), "144")
        self.assertEqual(window.display_page.get_display()["refresh_rate"], 144)

    def test_the_resolution_menu_follows_the_typed_size(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        page = window.display_page
        combo = page.resolution_combo
        self.assertEqual(combo.currentIndex(), -1, "1080 x 1920 is not one of the presets")
        page.width_edit.setText("1600")
        page.height_edit.setText("900")
        self.assertEqual(combo.currentText(), "1600 x 900")
        page.width_edit.setText("1234")
        self.assertEqual(combo.currentIndex(), -1)
        combo.setCurrentIndex(combo.findText("2560 x 1440"))
        self.assertEqual((page.width_edit.text(), page.height_edit.text()), ("2560", "1440"))

    def test_the_next_start_line_stays_short_when_many_things_changed(self):
        import bridge.ui.pages.home as home

        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        label = window.home_page.resume_reason_label
        many = ["the display settings changed", "the ROM library changed", "the emulator profile changed"]
        with unittest.mock.patch.object(home.start_iisu_pc, "describe_boot_fingerprint_diff", return_value=many):
            window.home_page.refresh_resume_reason()
        self.assertEqual(label.text(), "Next start: cold boot (3 things changed)")
        self.assertEqual(label.toolTip().splitlines(), many)
        with unittest.mock.patch.object(home.start_iisu_pc, "describe_boot_fingerprint_diff", return_value=many[:1]):
            window.home_page.refresh_resume_reason()
        self.assertEqual(label.text(), "Next start: cold boot (the display settings changed)")
        self.assertEqual(label.toolTip(), "")
        with unittest.mock.patch.object(home.start_iisu_pc, "describe_boot_fingerprint_diff", return_value=[]):
            window.home_page.refresh_resume_reason()
        self.assertEqual(label.text(), "Next start: quick resume")

    def test_credits_avatars_load_on_first_visit_only(self):
        import bridge.ui.pages.credits as credits

        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        cache = Path(self._tmp.name) / "avatars"
        with unittest.mock.patch.object(credits, "AVATAR_CACHE_DIR", cache), \
             unittest.mock.patch.object(credits, "run_in_background") as background:
            window = self._window()
            background.assert_not_called()
            page = window.pages["credits"]
            page.on_shown()
            fetched = [call.args[3] for call in background.call_args_list]
            page.on_shown()
        self.assertEqual(len(fetched), len(page._avatar_refs))
        self.assertEqual(len(background.call_args_list), len(fetched), "a second visit fetches nothing")

    def test_the_avd_name_is_hidden_on_waydroid(self):
        import bridge.ui.pages.settings.advanced as advanced

        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        with unittest.mock.patch.object(advanced, "resolve_backend", return_value=advanced.BACKEND_WAYDROID):
            window = self._window()
        self.assertTrue(window.advanced_page.avd_name_edit.isHidden())
        self.assertTrue(window.advanced_page.avd_name_label.isHidden())

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
