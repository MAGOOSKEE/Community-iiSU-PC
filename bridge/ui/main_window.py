"""The Manager's top-level window: sidebar + page container + shared Save
bar, replaces manager.py's Manager(tk.Tk) chrome (_build_ui/_build_
sidebar/_build_group_shell/_show_page/_show_subpage/_update_save_bar, plus
its config-reload and status-polling glue). Individual pages are ported one
at a time (see the plan); anything not yet ported shows a plain "coming
soon" placeholder so the app stays runnable throughout the rewrite.

Page-specific side effects (refreshing a page's own data when it becomes
visible) go through an `on_shown()` duck-typed hook instead of manager.py's
hardcoded _trigger_page_side_effects elif chain, each newly-ported page
just implements the method it needs, nothing here has to know its name in
advance.
"""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bridge.ui.pages.backup_diagnostics.backup_restore import BackupRestorePage
from bridge.ui.pages.backup_diagnostics.diagnostics import DiagnosticsPage
from bridge.ui.pages.credits import CreditsPage
from bridge.ui.pages.emulators.pc_emulators import EmulatorsPage
from bridge.ui.pages.games.console_browser import ConsoleBrowserPage
from bridge.ui.pages.games.windows_apps import WindowsAppsPage
from bridge.ui.pages.home import HomePage
from bridge.ui.pages.library.android_storage import AndroidStoragePage
from bridge.ui.pages.library.media_library import MediaLibraryPage
from bridge.ui.pages.library.roms import RomsPage
from bridge.ui.pages.settings.advanced import AdvancedPage
from bridge.ui.pages.settings.display import DisplayPage
from bridge.ui.pages.uninstall import UninstallPage
from bridge.ui.widgets.transitions import fade_in
from bridge.ui.sidebar import DANGER_NAV_ITEMS, LOCKED_NAV, NAV_GROUPS, NAV_ITEMS, Sidebar
from bridge_config import CONFIG_PATH, load_config, save_config
from shared.qt_theme import Fonts, GREEN, PANEL_BG_HOVER, RED, TEXT_DIM

# Sub-page keys (or top-level keys with no group) that use the shared
# bottom Save bar to commit straight to config.json.
SAVE_BAR_PAGES = {"roms", "emulators", "settings", "advanced"}


class _ComingSoonPage(QWidget):
    """Placeholder for a nav destination whose page hasn't been ported to
    Qt yet, keeps every nav entry clickable throughout the rewrite
    instead of hiding unported destinations."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel(label)
        title.setFont(Fonts.title())
        layout.addWidget(title)
        note = QLabel("Not yet ported to the new interface, coming soon.")
        note.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(note)
        layout.addStretch(1)


class ManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Community-iiSU-PC Manager")
        self.resize(1000, 700)
        self.setMinimumSize(880, 620)
        self._apply_window_icon()

        self.config_data: dict = {}
        self.configured = False
        self._config_mtime: float | None = None
        self.last_avd_up: bool | None = None
        self.last_bridge_up: bool | None = None
        self.current_page = "home"
        self.current_subpage: str | None = None
        self.group_current_sub: dict[str, str] = {}
        self.settings_dirty = False

        # Populated as each settings page is ported: key -> () -> bool
        # (True if that page's live inputs differ from config_data) and
        # key -> () -> bool (save it; returns whether the save succeeded).
        self.dirty_checkers: dict[str, callable] = {}
        self.save_handlers: dict[str, callable] = {}

        self._reload_config()

        central = QWidget()
        self.setCentralWidget(central)
        root_row = QHBoxLayout(central)
        root_row.setContentsMargins(0, 0, 0, 0)
        root_row.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.nav_clicked.connect(self._on_nav_click)
        self.sidebar.subnav_clicked.connect(self._on_subnav_click)
        self.sidebar.toggle_clicked.connect(self._toggle_sidebar)
        root_row.addWidget(self.sidebar)

        content_col = QWidget()
        content_layout = QVBoxLayout(content_col)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        root_row.addWidget(content_col, 1)

        self.page_stack = QStackedWidget()
        content_layout.addWidget(self.page_stack, 1)

        self.save_bar = QWidget()
        save_bar_layout = QHBoxLayout(self.save_bar)
        save_bar_layout.setContentsMargins(24, 14, 24, 14)
        self.save_status_label = QLabel("")
        self.save_status_label.setStyleSheet(f"color: {GREEN};")
        save_bar_layout.addWidget(self.save_status_label)
        save_bar_layout.addStretch(1)
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("accent")
        self.save_button.clicked.connect(self._on_save_clicked)
        save_bar_layout.addWidget(self.save_button)
        self.save_bar.setVisible(False)
        content_layout.addWidget(self.save_bar)

        self.pages: dict[str, QWidget] = {}
        self.subpages: dict[str, QWidget] = {}
        self.subpage_stacks: dict[str, QStackedWidget] = {}

        self.home_page = HomePage(self)
        self._register_page("home", self.home_page)

        for key, _icon, label in NAV_ITEMS[1:] + DANGER_NAV_ITEMS:
            if key in NAV_GROUPS:
                self._register_page(key, self._build_group_shell(key, label))
            elif key == "credits":
                self._register_page(key, CreditsPage(self))
            elif key == "uninstall":
                self._register_page(key, UninstallPage(self))
            else:
                self._register_page(key, _ComingSoonPage(label))

        for group_key, subs in NAV_GROUPS.items():
            for sub_key, sub_label in subs:
                self.register_subpage(sub_key, _ComingSoonPage(sub_label))

        # Ported settings pages replace their placeholders here as each one
        # is written, roms/emulators share one Save action and one dirty
        # flag (see _gather_settings), matching manager.py's original
        # single-config-write-covers-four-pages behavior.
        self.roms_page = RomsPage(self)
        self.register_subpage("roms", self.roms_page)
        self.emulators_page = EmulatorsPage(self)
        self.register_subpage("emulators", self.emulators_page)
        self.windows_apps_page = WindowsAppsPage(self)
        self.register_subpage("windows_apps", self.windows_apps_page)
        self.register_subpage("android_storage", AndroidStoragePage(self))
        self.register_subpage("media_library", MediaLibraryPage(self))
        self.register_subpage("games_console", ConsoleBrowserPage(self))
        self.display_page = DisplayPage(self)
        self.register_subpage("settings", self.display_page)
        self.advanced_page = AdvancedPage(self)
        self.register_subpage("advanced", self.advanced_page)
        self.register_subpage("backup_restore", BackupRestorePage(self))
        self.register_subpage("diagnostics", DiagnosticsPage(self))

        self._settings_rebuild_handlers = [
            self.roms_page.reload_from_config,
            self.emulators_page.reload_from_config,
            self.display_page.reload_from_config,
            self.advanced_page.reload_from_config,
        ]
        for key in ("roms", "emulators", "settings", "advanced"):
            self.dirty_checkers[key] = self._is_settings_dirty
            self.save_handlers[key] = self._save_settings

        self.refresh_nav_enabled()
        self.show_page("home")

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self.home_page.poll_status)
        self._status_timer.start(2000)

        self._dirty_timer = QTimer(self)
        self._dirty_timer.timeout.connect(self._poll_settings_dirty)
        self._dirty_timer.start(500)

    # == Style / icon ==

    def _apply_window_icon(self) -> None:
        import create_shortcut

        icon_path = (
            create_shortcut.EXTRACTED_ICON_PATH
            if create_shortcut.EXTRACTED_ICON_PATH.is_file()
            else create_shortcut.FALLBACK_ICON_PATH
        )
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

    # == Config state ==

    def _reload_config(self) -> None:
        if CONFIG_PATH.is_file():
            try:
                self.config_data = load_config()
                self.configured = True
                self._config_mtime = CONFIG_PATH.stat().st_mtime
                return
            except Exception:
                pass
        self.config_data = {}
        self.configured = False
        self._config_mtime = None

    def reload_config(self) -> None:
        self._reload_config()

    def _config_changed_on_disk(self) -> bool:
        if not CONFIG_PATH.is_file():
            return False
        try:
            return CONFIG_PATH.stat().st_mtime != self._config_mtime
        except OSError:
            return False

    def rebuild_settings_pages(self) -> None:
        """Placeholder for manager.py's _build_settings_pages, once the
        settings/ROMs/emulators pages are ported, each one registers a
        rebuild-from-config_data callback here instead of this method
        knowing their internals directly."""
        for handler in getattr(self, "_settings_rebuild_handlers", []):
            handler()

    def _gather_settings(self, silent: bool = False) -> dict | None:
        """Builds the settings dict exactly as Save would write it, ports
        manager.py's _gather_settings, but starting from a copy of the
        last-saved config_data rather than reading every field off self:
        only pages that exist yet override their own keys, so this grows
        as more settings pages get ported without touching the ones that
        already work. silent=True (the dirty-check timer) swallows invalid
        input instead of popping up a message box, a field being mid-
        edit shouldn't interrupt typing, it just reads as dirty until it's
        valid and saved."""
        settings = dict(self.config_data)
        if hasattr(self, "roms_page"):
            settings["roms_dir"] = self.roms_page.get_roms_dir()
            settings["search_roots"] = self.roms_page.get_search_roots()
        if hasattr(self, "emulators_page"):
            settings["emulators"] = self.emulators_page.get_emulators()

        if hasattr(self, "advanced_page"):
            port = self.advanced_page.get_port()
            if port is None:
                if silent:
                    return None
                QMessageBox.critical(self, "Invalid port", "Bridge listen port must be a number.")
                return None
            settings["bridge_port"] = port
            settings["iisu_window_title"] = self.advanced_page.get_window_title()
            settings["quit_hotkey"] = self.advanced_page.get_quit_hotkey()
            settings["shutdown_hotkey"] = self.advanced_page.get_shutdown_hotkey()
            settings["controller_quit_chord"] = self.advanced_page.get_controller_quit_chord()
            settings["show_boot_overlay"] = self.advanced_page.get_show_boot_overlay()
            settings["debug_show_console_windows"] = self.advanced_page.get_debug_show_console_windows()

        if hasattr(self, "display_page"):
            display = self.display_page.get_display()
            if display is None:
                if silent:
                    return None
                QMessageBox.critical(self, "Invalid display settings", "Width, height, density, and refresh rate must be numbers.")
                return None
            settings["display"] = display
            settings["iisu_fullscreen"] = self.display_page.get_fullscreen()
            settings["avd_name"] = self.display_page.get_avd_name()

        return settings

    def _is_settings_dirty(self) -> bool:
        if not any(hasattr(self, name) for name in ("roms_page", "emulators_page", "display_page", "advanced_page")):
            return False
        current = self._gather_settings(silent=True)
        if current is None:
            return False
        return current != {k: self.config_data.get(k) for k in current}

    def _save_settings(self) -> bool:
        settings = self._gather_settings()
        if settings is None:
            return False
        self.config_data = settings
        save_config(self.config_data)
        self._write_avd_display_profile(self.config_data)
        self.save_status_label.setText(f"Saved to {CONFIG_PATH.name}")
        self.save_status_label.setStyleSheet(f"color: {GREEN};")
        QTimer.singleShot(3000, lambda: self.save_status_label.setText(""))
        self._set_settings_dirty(False)
        self.home_page.refresh_resume_reason()
        return True

    def _write_avd_display_profile(self, config: dict) -> None:
        """Writes the chosen resolution/density straight into the AVD's own
        config.ini, the file emulator.exe actually reads at boot,
        without booting anything, same as onboarding_wizard.py's own copy
        of this. Save is locked out while the VM is running (see
        refresh_save_lock), so this only ever runs while it's stopped.
        Best-effort: a failure here just leaves the AVD on its previous
        profile until this runs again successfully."""
        if "display" not in config:
            return
        import apply_display

        config_ini = apply_display.avd_config_path(config.get("avd_name", "iisuwin"))
        if not config_ini.is_file():
            return
        try:
            apply_display.update_config_ini(config_ini, config["display"])
        except OSError as e:
            print(f"[manager] couldn't write the AVD's display profile ({e})")

    # == Chrome: sidebar + page container ==

    def _register_page(self, key: str, widget: QWidget) -> None:
        self.pages[key] = widget
        self.page_stack.addWidget(widget)

    def register_subpage(self, sub_key: str, widget: QWidget) -> None:
        """Swaps a placeholder for a real ported page, called by whatever
        sets up that page (currently nothing; each future page-porting
        step will call this once its widget exists)."""
        stack = self.subpage_stacks[self._group_for_sub(sub_key)]
        if sub_key in self.subpages:
            old = self.subpages[sub_key]
            stack.removeWidget(old)
            old.deleteLater()
        stack.addWidget(widget)
        self.subpages[sub_key] = widget

    def _group_for_sub(self, sub_key: str) -> str:
        for group_key, subs in NAV_GROUPS.items():
            if any(sub_key == sk for sk, _label in subs):
                return group_key
        raise KeyError(sub_key)

    def _build_group_shell(self, group_key: str, _label: str) -> QWidget:
        shell = QWidget()
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Pill row + divider get their own inset container (rather than a
        # QSS "margin" on the divider itself, which a fixed-height QFrame
        # doesn't reliably honor inside a layout) so there's real breathing
        # room above and below the line, not just a bare 1px rule glued to
        # the tab row.
        header_area = QWidget()
        header_layout = QVBoxLayout(header_area)
        header_layout.setContentsMargins(24, 16, 24, 0)
        header_layout.setSpacing(0)

        subnav_row = QWidget()
        header_layout.addWidget(subnav_row)
        self.sidebar.build_subnav(group_key, subnav_row)

        header_layout.addSpacing(10)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {PANEL_BG_HOVER};")
        header_layout.addWidget(divider)

        layout.addWidget(header_area)
        layout.addSpacing(10)

        stack = QStackedWidget()
        layout.addWidget(stack, 1)
        self.subpage_stacks[group_key] = stack
        return shell

    def _toggle_sidebar(self) -> None:
        self.sidebar.set_expanded(not self.sidebar.is_expanded())

    def _on_nav_click(self, key: str) -> None:
        if key == self.current_page:
            return
        if not self._confirm_leave_unsaved_settings():
            return
        if key in LOCKED_NAV:
            if not self.configured:
                return
            if self._config_changed_on_disk():
                self._reload_config()
                self.rebuild_settings_pages()
        self.show_page(key)

    def _on_subnav_click(self, group_key: str, sub_key: str) -> None:
        if group_key == self.current_page and sub_key == self.current_subpage:
            return
        if not self._confirm_leave_unsaved_settings():
            return
        if group_key in LOCKED_NAV and self._config_changed_on_disk():
            self._reload_config()
            self.rebuild_settings_pages()
        self._show_subpage(group_key, sub_key)

    def refresh_nav_enabled(self) -> None:
        for key in LOCKED_NAV:
            self.sidebar.set_nav_enabled(key, self.configured)

    def show_page(self, key: str) -> None:
        self.current_page = key
        self.sidebar.set_current(key)
        self.page_stack.setCurrentWidget(self.pages[key])
        fade_in(self.pages[key])
        if key in NAV_GROUPS:
            sub_key = self.group_current_sub.get(key, NAV_GROUPS[key][0][0])
            self._show_subpage(key, sub_key)
        else:
            self.current_subpage = None
            self._update_save_bar(key)
            self._trigger_page_side_effects(key)

    def _show_subpage(self, group_key: str, sub_key: str) -> None:
        self.current_subpage = sub_key
        self.group_current_sub[group_key] = sub_key
        self.sidebar.set_current_sub(group_key, sub_key)
        self.subpage_stacks[group_key].setCurrentWidget(self.subpages[sub_key])
        fade_in(self.subpages[sub_key])
        self._update_save_bar(sub_key)
        self._trigger_page_side_effects(sub_key)

    def _update_save_bar(self, key: str) -> None:
        self.save_bar.setVisible(key in SAVE_BAR_PAGES)

    def _trigger_page_side_effects(self, key: str) -> None:
        page = self.pages.get(key) or self.subpages.get(key)
        on_shown = getattr(page, "on_shown", None)
        if callable(on_shown):
            on_shown()

    # == Save bar / dirty tracking ==

    def _visible_save_key(self) -> str | None:
        return self.current_subpage if self.current_page in NAV_GROUPS else self.current_page

    def _on_save_clicked(self) -> None:
        key = self._visible_save_key()
        handler = self.save_handlers.get(key)
        if handler is not None:
            handler()

    def _poll_settings_dirty(self) -> None:
        key = self._visible_save_key()
        checker = self.dirty_checkers.get(key) if key in SAVE_BAR_PAGES else None
        dirty = bool(checker()) if checker is not None else False
        if dirty != self.settings_dirty:
            self._set_settings_dirty(dirty)

    def _set_settings_dirty(self, dirty: bool) -> None:
        self.settings_dirty = dirty
        self.save_button.setText("Save • unsaved changes" if dirty else "Save")
        self.save_button.setProperty("dirty", "true" if dirty else "false")
        self.save_button.style().unpolish(self.save_button)
        self.save_button.style().polish(self.save_button)

    def _confirm_leave_unsaved_settings(self) -> bool:
        if not self.settings_dirty:
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            "This page has unsaved changes. Save them before leaving?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Yes:
            key = self._visible_save_key()
            handler = self.save_handlers.get(key)
            return bool(handler()) if handler is not None else True
        self._set_settings_dirty(False)
        self.rebuild_settings_pages()
        return True

    # == Save/lock (called by HomePage's status poll) ==

    def refresh_save_lock(self, avd_up: bool | None, bridge_up: bool | None) -> None:
        running = bool(avd_up) or bool(bridge_up)
        self.save_button.setEnabled(not running)
        if running:
            self.save_status_label.setText("Stop Community-iiSU-PC to change settings")
            self.save_status_label.setStyleSheet(f"color: {RED};")
        elif self.save_status_label.text() == "Stop Community-iiSU-PC to change settings":
            self.save_status_label.setText("")
            self.save_status_label.setStyleSheet(f"color: {GREEN};")

    # == Close ==

    def closeEvent(self, event) -> None:
        if self.home_page.confirm_close():
            event.accept()
        else:
            event.ignore()
