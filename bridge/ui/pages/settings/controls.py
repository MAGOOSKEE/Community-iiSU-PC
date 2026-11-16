"""Controls settings page: the keys and controller buttons that quit a game
or shut everything down. Split out of the Advanced page, where these everyday
settings sat among the expert ones (port, window title, debugging)."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QVBoxLayout, QWidget

from bridge.ui.onboarding_wizard import HotkeyEditor
from bridge.ui.pages.base import PageBase
from controller_bridge import BUTTON_DISPLAY_NAMES, BUTTON_NAME_TO_BIT, DEFAULT_QUIT_CHORD
from shared.qt_theme import TEXT_DIM


class ControlsPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header("Controls", "How to leave a game, and how to shut everything down, from the keyboard or a controller.")

        self._quit_hotkey_editor: HotkeyEditor | None = None
        self._shutdown_hotkey_editor: HotkeyEditor | None = None
        self._hotkey_container = QWidget()
        self._hotkey_layout = QVBoxLayout(self._hotkey_container)
        self._hotkey_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.addWidget(self._hotkey_container)

        self.body_layout.addWidget(QLabel("Controller buttons that quit a game (hold them all at once):"))
        chord_row = QWidget()
        chord_grid = QGridLayout(chord_row)
        chord_grid.setContentsMargins(0, 0, 0, 0)
        self._chord_checks: dict[str, QCheckBox] = {}
        for index, name in enumerate(BUTTON_NAME_TO_BIT):
            check = QCheckBox(BUTTON_DISPLAY_NAMES.get(name, name))
            self._chord_checks[name] = check
            chord_grid.addWidget(check, index // 7, index % 7)
        self.body_layout.addWidget(chord_row)
        chord_note = QLabel("Does the same as the quit key. Untick them all to turn it off.")
        chord_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(chord_note)

        restart_note = QLabel("Changes apply the next time iiSU starts.")
        restart_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(restart_note)
        self.body_layout.addStretch(1)

        self.reload_from_config()

    def reload_from_config(self) -> None:
        config = self.window.config_data
        while self._hotkey_layout.count():
            item = self._hotkey_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._quit_hotkey_editor = HotkeyEditor(
            "Quit key (closes the game and goes back to iiSU; in iiSU, shuts everything down):",
            config.get("quit_hotkey", {"modifiers": [], "key": "escape"}),
            default_key="escape",
        )
        self._hotkey_layout.addWidget(self._quit_hotkey_editor)
        self._shutdown_hotkey_editor = HotkeyEditor(
            "Shut everything down from anywhere (optional):",
            config.get("shutdown_hotkey") or {"modifiers": [], "key": ""},
            default_key="",
        )
        self._hotkey_layout.addWidget(self._shutdown_hotkey_editor)

        initial_chord = set(config.get("controller_quit_chord") or DEFAULT_QUIT_CHORD)
        for name, check in self._chord_checks.items():
            check.setChecked(name in initial_chord)

    def get_quit_hotkey(self) -> dict:
        return self._quit_hotkey_editor.read_hotkey()

    def get_shutdown_hotkey(self) -> dict | None:
        return self._shutdown_hotkey_editor.read_hotkey() if self._shutdown_hotkey_editor.get_key() else None

    def get_controller_quit_chord(self) -> list[str]:
        return [name for name, check in self._chord_checks.items() if check.isChecked()]
