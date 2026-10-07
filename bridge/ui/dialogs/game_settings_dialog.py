"""Per-game settings dialog (Console page, right-click > Game Settings...).
Edits one entry of config.json's "game_overrides", see bridge/game_overrides.py
for what each field does. Changes apply on that game's next launch."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

import game_overrides as go
from shared.emulator_defaults import all_stub_packages
from shared.qt_theme import TEXT_DIM

DEFAULT_EMULATOR_TEXT = "Default for this console"


class GameSettingsDialog(QDialog):
    def __init__(self, rom_filename: str, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Game Settings")
        self.setMinimumWidth(520)
        self.rom_filename = rom_filename
        self.result_override: dict | None = None  # set on Save; {} means "cleared"

        layout = QVBoxLayout(self)
        title = QLabel(rom_filename)
        title.setWordWrap(True)
        layout.addWidget(title)
        note = QLabel("Applies only to this game, on its next launch. Leave a field empty to use the normal behavior.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(note)

        current = go.get_override(config, rom_filename) or go.normalize_override(None)

        labels = dict(all_stub_packages())
        form = QFormLayout()
        self.emulator_combo = QComboBox()
        self.emulator_combo.addItem(DEFAULT_EMULATOR_TEXT, "")
        for prefix in go.selectable_emulators(config.get("emulators", {})):
            self.emulator_combo.addItem(f"{labels.get(prefix, prefix)} ({prefix})", prefix)
        index = self.emulator_combo.findData(current["emulator"])
        if index < 0 and current["emulator"]:
            # An emulator that's no longer configured: keep showing it so
            # saving doesn't silently drop the choice.
            self.emulator_combo.addItem(f"{current['emulator']} (not configured)", current["emulator"])
            index = self.emulator_combo.count() - 1
        self.emulator_combo.setCurrentIndex(max(0, index))
        form.addRow("Emulator:", self.emulator_combo)

        self.extra_args_edit = QLineEdit(current["extra_args"])
        self.extra_args_edit.setPlaceholderText("e.g. -fullscreen -nogui")
        form.addRow("Extra launch flags:", self.extra_args_edit)

        self.env_edit = QPlainTextEdit(current["env"])
        self.env_edit.setPlaceholderText("One KEY=VALUE per line")
        self.env_edit.setFixedHeight(64)
        form.addRow("Environment variables:", self.env_edit)

        self.pre_launch_edit = QLineEdit(current["pre_launch"])
        self.pre_launch_edit.setPlaceholderText("A program to run and wait for before the game starts")
        form.addRow("Run before launch:", self.pre_launch_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        clear_button = QPushButton("Clear Overrides")
        clear_button.setObjectName("ghost")
        clear_button.clicked.connect(self._clear)
        buttons.addButton(clear_button, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        self.result_override = go.normalize_override({
            "emulator": self.emulator_combo.currentData(),
            "extra_args": self.extra_args_edit.text(),
            "env": self.env_edit.toPlainText(),
            "pre_launch": self.pre_launch_edit.text(),
        })
        self.accept()

    def _clear(self) -> None:
        self.result_override = go.normalize_override(None)
        self.accept()
