"""Advanced settings page, ports manager.py's _build_advanced_page.
Reuses HotkeyEditor/KeyCaptureDialog from onboarding_wizard.py rather than
re-implementing hotkey capture a second time (its own docstring already
anticipated this reuse)."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bridge.ui.onboarding_wizard import HotkeyEditor
from bridge.ui.pages.base import PageBase
from controller_bridge import BUTTON_DISPLAY_NAMES, BUTTON_NAME_TO_BIT, DEFAULT_QUIT_CHORD
from shared.qt_theme import TEXT_DIM


class AdvancedPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header("Advanced", "Bridge port, window matching, and hotkeys, rarely need to change these.")

        self.body_layout.addWidget(QLabel("iiSU window title match:"))
        self.window_title_edit = QLineEdit()
        self.window_title_edit.setFixedWidth(320)
        self.body_layout.addWidget(self.window_title_edit)

        self.body_layout.addWidget(QLabel("Bridge listen port:"))
        self.port_edit = QLineEdit()
        self.port_edit.setFixedWidth(80)
        self.body_layout.addWidget(self.port_edit)

        self._quit_hotkey_editor: HotkeyEditor | None = None
        self._shutdown_hotkey_editor: HotkeyEditor | None = None
        self._hotkey_container = QWidget()
        self._hotkey_layout = QVBoxLayout(self._hotkey_container)
        self._hotkey_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.addWidget(self._hotkey_container)

        self.body_layout.addWidget(QLabel("Controller quit chord (press all selected buttons together on any pad):"))
        chord_row = QWidget()
        chord_grid = QGridLayout(chord_row)
        chord_grid.setContentsMargins(0, 0, 0, 0)
        self._chord_checks: dict[str, QCheckBox] = {}
        for index, name in enumerate(BUTTON_NAME_TO_BIT):
            check = QCheckBox(BUTTON_DISPLAY_NAMES.get(name, name))
            self._chord_checks[name] = check
            chord_grid.addWidget(check, index // 7, index % 7)
        self.body_layout.addWidget(chord_row)
        chord_note = QLabel("Runs the same action as tapping the quit key above. Leave every box unchecked to disable it.")
        chord_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(chord_note)

        restart_note = QLabel(
            "Port and hotkey changes need the bridge restarted to take effect (ROM\n"
            "directory, search folders, and emulator mappings apply on the very next\n"
            "game launch, no restart needed)."
        )
        restart_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(restart_note)

        self.show_overlay_check = QCheckBox("Show the fullscreen loading overlay during boot and game hand-off")
        self.body_layout.addWidget(self.show_overlay_check)

        self.debug_console_check = QCheckBox("Show console windows for the AVD and bridge (debugging)")
        self.body_layout.addWidget(self.debug_console_check)
        debug_note = QLabel(
            "Console windows are off by default, logging to emulator.log/bridge.log/stop.log instead.\n"
            "Turning them on always hides the overlay too, since it would just cover them up, and trades\n"
            "away that run's log file since a process can't sensibly have both. Takes effect on the next Start."
        )
        debug_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(debug_note)

        self.body_layout.addWidget(QLabel("Steam:"))
        steam_row = QHBoxLayout()
        add_steam_button = QPushButton("Add to Steam")
        add_steam_button.setObjectName("ghost")
        add_steam_button.clicked.connect(self._add_to_steam)
        steam_row.addWidget(add_steam_button)
        remove_steam_button = QPushButton("Remove from Steam")
        remove_steam_button.setObjectName("ghost")
        remove_steam_button.clicked.connect(self._remove_from_steam)
        steam_row.addWidget(remove_steam_button)
        steam_row.addStretch(1)
        self.body_layout.addLayout(steam_row)
        self.steam_note = QLabel(
            "Adds Community-iiSU-PC as a non-Steam game so you can start it from Steam's library, Big "
            "Picture, or a Steam Deck's Game Mode. Close Steam first, it rewrites this list when it exits. "
            "The shortcut starts the VM and bridge and then ends, so Steam shows it as stopped right away "
            "while iiSU keeps running."
        )
        self.steam_note.setWordWrap(True)
        self.steam_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.steam_note)
        self.body_layout.addStretch(1)

        self.reload_from_config()

    def reload_from_config(self) -> None:
        config = self.window.config_data
        self.window_title_edit.setText(config.get("iisu_window_title", ""))
        self.port_edit.setText(str(config.get("bridge_port", 7737)))

        while self._hotkey_layout.count():
            item = self._hotkey_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._quit_hotkey_editor = HotkeyEditor(
            "Quit key (tap to force-quit the running emulator and return to iiSU):",
            config.get("quit_hotkey", {"modifiers": [], "key": "escape"}),
            default_key="escape",
        )
        self._hotkey_layout.addWidget(self._quit_hotkey_editor)
        self._shutdown_hotkey_editor = HotkeyEditor(
            "Optional separate full-shutdown hotkey (leave blank for none):",
            config.get("shutdown_hotkey") or {"modifiers": [], "key": ""},
            default_key="",
        )
        self._hotkey_layout.addWidget(self._shutdown_hotkey_editor)

        initial_chord = set(config.get("controller_quit_chord") or DEFAULT_QUIT_CHORD)
        for name, check in self._chord_checks.items():
            check.setChecked(name in initial_chord)

        self.show_overlay_check.setChecked(bool(config.get("show_boot_overlay", True)))
        self.debug_console_check.setChecked(bool(config.get("debug_show_console_windows", False)))

    # == Field getters (used by ManagerWindow._gather_settings) ==

    def get_window_title(self) -> str:
        return self.window_title_edit.text().strip()

    def get_port(self) -> int | None:
        try:
            return int(self.port_edit.text())
        except ValueError:
            return None

    def get_quit_hotkey(self) -> dict:
        return self._quit_hotkey_editor.read_hotkey()

    def get_shutdown_hotkey(self) -> dict | None:
        return self._shutdown_hotkey_editor.read_hotkey() if self._shutdown_hotkey_editor.get_key() else None

    def get_controller_quit_chord(self) -> list[str]:
        return [name for name, check in self._chord_checks.items() if check.isChecked()]

    def get_show_boot_overlay(self) -> bool:
        return self.show_overlay_check.isChecked()

    def get_debug_show_console_windows(self) -> bool:
        return self.debug_console_check.isChecked()

    # == Steam shortcut ==

    def _add_to_steam(self) -> None:
        from bridge.services import steam_shortcuts_service as steam

        try:
            results = steam.add_to_steam()
        except steam.SteamShortcutError as e:
            QMessageBox.warning(self, "Add to Steam", str(e))
            return
        except OSError as e:
            QMessageBox.critical(self, "Add to Steam", f"Couldn't write Steam's shortcut list:\n\n{e}")
            return
        words = {"added": "Added", "updated": "Updated"}
        summary = "\n".join(f"{words[outcome]}: {path}" for path, outcome in results)
        QMessageBox.information(
            self, "Add to Steam",
            f"{summary}\n\nStart Steam and look for \"{steam.SHORTCUT_NAME}\" in your library. "
            "The previous shortcut list was kept as a .bak file next to it.",
        )

    def _remove_from_steam(self) -> None:
        from bridge.services import steam_shortcuts_service as steam

        try:
            changed = steam.remove_from_steam()
        except steam.SteamShortcutError as e:
            QMessageBox.warning(self, "Remove from Steam", str(e))
            return
        except OSError as e:
            QMessageBox.critical(self, "Remove from Steam", f"Couldn't write Steam's shortcut list:\n\n{e}")
            return
        QMessageBox.information(self, "Remove from Steam", "Removed it from Steam." if changed else "It wasn't in Steam's shortcut list.")
