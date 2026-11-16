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

        # iiSU counts as patched with the current patch, unless a test says otherwise.
        from shared import iisu_install_record

        self.record_path = Path(self._tmp.name) / ".iisu_installed.json"
        iisu_install_record.save("iiSU-Alpha-7.5.apk", "0.1.6.1", self.record_path)
        record_patch = unittest.mock.patch.object(iisu_install_record.load, "__defaults__", (self.record_path,))
        record_patch.start()
        self.addCleanup(record_patch.stop)

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
        self.assertEqual(combo.currentText(), "Custom", "1080 x 1920 is not one of the presets")
        page.width_edit.setText("1600")
        page.height_edit.setText("900")
        self.assertEqual(combo.currentText(), "1600 x 900")
        page.width_edit.setText("1234")
        self.assertEqual(combo.currentText(), "Custom")
        combo.setCurrentIndex(combo.findText("2560 x 1440"))
        self.assertEqual((page.width_edit.text(), page.height_edit.text()), ("2560", "1440"))
        combo.setCurrentIndex(combo.findText("Custom"))
        self.assertEqual((page.width_edit.text(), page.height_edit.text()), ("2560", "1440"), "Custom changes nothing")
        self.assertEqual(combo.currentText(), "Custom")

    def test_the_menu_button_says_what_the_next_start_will_be(self):
        import bridge.ui.pages.home as home

        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        button = window.home_page.advanced_button
        self.assertFalse(hasattr(window.home_page, "resume_reason_label"), "no longer a line on the page")
        many = ["the display settings changed", "the ROM library changed", "the emulator profile changed"]
        with unittest.mock.patch.object(home.start_iisu_pc, "describe_boot_fingerprint_diff", return_value=many):
            window.home_page.refresh_resume_reason()
        self.assertEqual(button.toolTip().splitlines(), ["Next start: full restart (3 things changed)", *many])
        with unittest.mock.patch.object(home.start_iisu_pc, "describe_boot_fingerprint_diff", return_value=many[:1]):
            window.home_page.refresh_resume_reason()
        self.assertEqual(button.toolTip(), "Next start: full restart (the display settings changed)")
        # Quick resume also needs a complete snapshot, which a CI runner has none of.
        with unittest.mock.patch.object(home.start_iisu_pc, "describe_boot_fingerprint_diff", return_value=[]), \
                unittest.mock.patch.object(home.start_iisu_pc, "has_complete_quickboot_snapshot", return_value=True):
            window.home_page.refresh_resume_reason()
        self.assertEqual(button.toolTip(), "Next start: quick (resumes where Android left off)")

    def test_the_menu_next_to_start_holds_steam_and_the_show_logs_tick_box(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        page = window.home_page
        self.assertFalse(hasattr(page, "logs_toggle_button"), "no Show Logs button on the page itself")
        self.assertFalse(hasattr(window.advanced_page, "_add_to_steam"), "Steam moved off Settings > Advanced")
        self.assertTrue(page.log_drawer.isHidden())

        menu = page._build_advanced_menu()
        self.addCleanup(menu.deleteLater)
        actions = {action.text(): action for action in menu.actions() if action.text()}
        for name in ("Add to Steam", "Remove from Steam"):
            with self.subTest(name=name):
                self.assertTrue(actions[name].isEnabled())
        show_logs = actions["Show Logs"]
        self.assertTrue(show_logs.isCheckable())
        self.assertFalse(show_logs.isChecked())

        was_dirty = window._is_settings_dirty()
        show_logs.setChecked(True)
        self.assertFalse(page.log_drawer.isHidden())
        self.assertIs(json.loads(self.config_path.read_text(encoding="utf-8"))["home_show_logs"], True)
        self.assertNotIn("home_show_logs", window._gather_settings(silent=True).keys() - window.config_data.keys())
        self.assertEqual(window._is_settings_dirty(), was_dirty, "remembering it is not an unsaved edit")

        # Still ticked in a menu opened later, and in a Manager opened later.
        again = page._build_advanced_menu()
        self.addCleanup(again.deleteLater)
        self.assertTrue({a.text(): a for a in again.actions()}["Show Logs"].isChecked())
        self.assertFalse(self._window().home_page.log_drawer.isHidden())

        show_logs.setChecked(False)
        self.assertTrue(page.log_drawer.isHidden())
        self.assertIs(json.loads(self.config_path.read_text(encoding="utf-8"))["home_show_logs"], False)

    def test_the_tagline_is_one_of_the_list(self):
        import bridge.ui.pages.home as home

        window = self._window()
        self.assertIn(window.home_page.tagline_label.text(), home.TAGLINES)
        self.assertIn("It's game time!", home.TAGLINES)

    def test_the_collapsed_sidebar_shows_the_mark_and_the_short_version(self):
        import bridge.ui.sidebar as sidebar

        self.assertEqual(sidebar.short_version("v0.6.0-alpha"), "v0.6.0")
        self.assertEqual(sidebar.short_version("dev"), "dev")
        with unittest.mock.patch.object(sidebar.updater, "get_installed_version", return_value="v0.6.0-alpha"):
            window = self._window()
        bar = window.sidebar
        labels = {key: label for key, _glyph, label in sidebar.NAV_ITEMS}
        self.assertEqual(labels["backup_diagnostics"], "Backup & Tools")
        self.assertEqual(bar._header_title.text(), "MANAGER")
        self.assertFalse(bar._header_title.isHidden())
        self.assertEqual(bar.version_label.text().strip(), "v0.6.0-alpha")

        bar.set_expanded(False)
        self.assertEqual(bar.maximumWidth(), sidebar.SIDEBAR_WIDTH_COLLAPSED)
        self.assertTrue(bar._header_title.isHidden())
        self.assertFalse(bar.version_label.isHidden(), "the version stays, shortened")
        self.assertEqual(bar.version_label.text(), "v0.6.0")
        if bar._header_mark.is_available():
            self.assertFalse(bar._header_mark.isHidden(), "the mark is the collapsed header")
            self.assertTrue(bar._header_button.icon().isNull(), "in place of the menu icon")

        bar.set_expanded(True)
        self.assertFalse(bar._header_title.isHidden())
        self.assertFalse(bar._header_button.icon().isNull())
        self.assertEqual(bar.version_label.text().strip(), "v0.6.0-alpha")

    def test_the_controller_notice_remembers_dont_show_again(self):
        import controller_bridge
        import bridge.ui.controller_notice_app as notice

        state_path = Path(self._tmp.name) / ".controller_notice.json"
        with unittest.mock.patch.object(controller_bridge, "CONTROLLER_NOTICE_STATE_PATH", state_path):
            box = notice.build_notice("DualSense Wireless Controller")
            self.addCleanup(box.deleteLater)
            self.assertIn("DualSense Wireless Controller", box.text())
            self.assertIs(box.checkBox(), box.dont_show_again)
            notice.finish(box)
            self.assertFalse(controller_bridge.controller_notice_dismissed(), "closed without ticking the box")
            box.dont_show_again.setChecked(True)
            notice.finish(box)
            self.assertTrue(controller_bridge.controller_notice_dismissed())

    def test_long_status_lines_never_overlap_the_start_button(self):
        from PySide6.QtCore import QRect

        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        window.show_page("home")
        page = window.home_page
        # The line that cut the button in half, at the window's smallest size.
        page.stage_label.setText("Done in 4s. Bridge and Android VM should both be fully stopped now. " * 2)
        window.resize(window.minimumSizeHint())
        window.show()
        self.app.processEvents()

        def rect(widget):
            return QRect(widget.mapTo(window, widget.rect().topLeft()), widget.size())

        button = rect(page.primary_button)
        self.assertGreaterEqual(button.height(), page.primary_button.minimumHeight())
        label = page.stage_label
        self.assertFalse(rect(label).intersects(button))
        self.assertEqual(label.height(), label.fontMetrics().height(), "one line, never wrapped")
        self.assertIn("Bridge and Android VM", page.stage_label.toolTip(), "the full text is in the tooltip")

    def test_an_iisu_on_an_older_patch_must_be_repatched_before_it_starts(self):
        import bridge.ui.dialogs.repatch_wizard as repatch_wizard

        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        # A record from before patch versions were kept: an install made before this update.
        self.record_path.write_text(json.dumps({"apk_name": "iiSU-Alpha-7.4.apk", "version_name": "0.1.6.1"}), encoding="utf-8")
        window = self._window()
        window.last_avd_up = window.last_bridge_up = False
        page = window.home_page
        page.refresh_primary_button()
        self.assertEqual(page.primary_button.text(), "Repatch iiSU")
        with unittest.mock.patch.object(repatch_wizard.RepatchWizard, "exec") as shown, \
             unittest.mock.patch.object(page, "_run_guarded") as started:
            page._start()
            page._start(headless=True)
        self.assertEqual(shown.call_count, 2, "Start and Boot Headless both open the wizard")
        started.assert_not_called()

    def test_the_repatch_wizard_offers_each_source_and_waits_for_a_stopped_vm(self):
        import bridge.ui.dialogs.repatch_wizard as repatch_wizard

        apk = Path(self._tmp.name) / "iiSU-Alpha-7.4.apk"
        apk.write_bytes(b"apk")
        self.record_path.write_text(json.dumps({"apk_name": apk.name, "version_name": "0.1.6.1"}), encoding="utf-8")
        running = {"vm": True}
        with unittest.mock.patch.object(repatch_wizard.svc, "find_recorded_apk", return_value=apk), \
             unittest.mock.patch.object(repatch_wizard, "run_in_background"):
            wizard = repatch_wizard.RepatchWizard(lambda: running["vm"])
        self.addCleanup(wizard.deleteLater)
        self.assertTrue(wizard.recorded_option.isChecked())
        self.assertEqual(wizard.selected_source(), apk)
        self.assertFalse(wizard.repatch_button.isEnabled(), "the VM is running")
        running["vm"] = False
        wizard._refresh()
        self.assertTrue(wizard.repatch_button.isEnabled())
        wizard._on_latest(repatch_wizard.svc.IisuReleaseInfo("0.0.7.5", "https://x/iiSU-Alpha-7.5.apk", "iiSU-Alpha-7.5.apk", "https://x"))
        wizard.latest_option.setChecked(True)
        self.assertEqual(wizard.selected_source(), "https://x/iiSU-Alpha-7.5.apk", "a URL is downloaded first")

    def test_recreate_shortcut_asks_for_the_icon_and_uses_the_choice(self):
        import create_shortcut
        import bridge.ui.dialogs.icon_choice_dialog as icon_choice_dialog
        import bridge.ui.pages.home as home
        from PySide6.QtWidgets import QDialog

        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        window = self._window()
        page = window.home_page
        with unittest.mock.patch.object(create_shortcut, "CONFIG_PATH", self.config_path), \
             unittest.mock.patch.object(icon_choice_dialog.IconChoiceDialog, "exec", return_value=QDialog.DialogCode.Rejected), \
             unittest.mock.patch.object(home, "run_in_background") as remade:
            page._recreate_desktop_shortcut()
        remade.assert_not_called()
        self.assertNotIn("icon_choice", json.loads(self.config_path.read_text(encoding="utf-8")), "Cancel changes nothing")

        with unittest.mock.patch.object(create_shortcut, "CONFIG_PATH", self.config_path), \
             unittest.mock.patch.object(icon_choice_dialog.IconChoiceDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             unittest.mock.patch.object(icon_choice_dialog.IconChoiceDialog, "choice", return_value="iipc"), \
             unittest.mock.patch.object(home, "run_in_background") as remade:
            page._recreate_desktop_shortcut()
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["icon_choice"], "iipc")
        self.assertEqual(remade.call_args.args[-1], "iipc", "the shortcuts are remade with the choice")

    def test_the_icon_popup_offers_iisus_icon_only_once_extracted(self):
        import create_shortcut
        from bridge.ui.dialogs.icon_choice_dialog import IconChoiceDialog

        missing = Path(self._tmp.name) / ".iisu_icon.png"
        with unittest.mock.patch.object(create_shortcut, "EXTRACTED_PNG_PATH", missing):
            dialog = IconChoiceDialog(None)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.iisu_option.isEnabled())
        self.assertEqual(dialog.choice(), "iipc")

        missing.write_bytes(create_shortcut.IIPC_PNG_PATH.read_bytes())
        with unittest.mock.patch.object(create_shortcut, "EXTRACTED_PNG_PATH", missing):
            dialog = IconChoiceDialog(None)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.iisu_option.isEnabled())
        self.assertEqual(dialog.choice(), "iisu", "iiSU's own stays the default")
        self.assertTrue(dialog.shortcut_check.isHidden(), "only Setup's popup offers no shortcut")

        dialog = IconChoiceDialog(None, offer_no_shortcut=True)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.shortcut_check.isHidden())
        self.assertTrue(dialog.wants_shortcut(), "a shortcut stays the default")

    def test_a_left_click_on_the_version_opens_its_release_page(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        import bridge.ui.sidebar as sidebar

        window = self._window()
        label = window.sidebar.version_label
        with unittest.mock.patch.object(sidebar.updater, "get_installed_version", return_value="v0.5.7-alpha"), \
             unittest.mock.patch.object(sidebar, "open_uri") as opened:
            QTest.mouseClick(label, Qt.MouseButton.RightButton, pos=QPoint(5, 5))
            opened.assert_not_called()
            QTest.mouseClick(label, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        opened.assert_called_once_with("https://github.com/MAGOOSKEE/Community-iiSU-PC/releases/tag/v0.5.7-alpha")
        self.assertEqual(sidebar.release_url(None), "https://github.com/MAGOOSKEE/Community-iiSU-PC/releases")

    def test_setups_screen_step_is_laid_out_like_the_display_page(self):
        from bridge.ui.onboarding_wizard import DisplayStep

        step = DisplayStep({"width": 1920, "height": 1080, "refresh_rate": 144})
        self.addCleanup(step.deleteLater)
        self.assertEqual(step.resolution_combo.currentText(), "1920 x 1080", "the preset follows the typed size")
        combo = step.refresh_edit.parent()
        self.assertTrue(combo.isEditable(), "one editable refresh rate box, not a field and a menu")
        self.assertEqual(step.refresh_edit.text(), "144")
        combo.setCurrentIndex(combo.findText("60"))
        self.assertEqual(step.refresh_edit.text(), "60")
        step.resolution_combo.setCurrentIndex(step.resolution_combo.findText("2560 x 1440"))
        self.assertEqual((step.width_edit.text(), step.height_edit.text()), ("2560", "1440"))

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
