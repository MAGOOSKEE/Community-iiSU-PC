"""Picks which PC emulator one console launches: a known emulator from a
list, or any other program. The plain-language replacement for typing an
Android package prefix, executable names and comma-separated flags into
EmulatorDialog, which stays available behind the PC Emulators page's
"Show the raw mapping table" for anything this can't express."""

import sys
from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from bridge.services.emulator_overview_service import ConsoleRow, emulator_choices
from shared.qt_theme import SPACING_MD, SPACING_SM, TEXT_DIM

OTHER_PROGRAM = "Another program..."


class ChangeEmulatorDialog(QDialog):
    def __init__(self, row: ConsoleRow, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Emulator for {row.console_label}")
        self.setMinimumWidth(480)
        self._choices = emulator_choices()
        self._program_path: Path | None = None
        # (exe_names, pre_args, rom_before_args, folder to add to the search folders or None)
        self.result_values: tuple[list[str], list[str], bool, Path | None] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        layout.addWidget(QLabel(f"Which emulator should {row.console_label} games open in?"))
        self.combo = QComboBox()
        for choice in self._choices:
            self.combo.addItem(choice["label"])
        self.combo.addItem(OTHER_PROGRAM)
        current = self.combo.findText(row.emulator)
        self.combo.setCurrentIndex(current if current >= 0 else self.combo.count() - 1)
        layout.addWidget(self.combo)

        program_row = QHBoxLayout()
        self.program_label = QLabel("")
        self.program_label.setWordWrap(True)
        self.program_label.setStyleSheet(f"color: {TEXT_DIM};")
        program_row.addWidget(self.program_label, 1)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.setObjectName("ghost")
        self.browse_button.clicked.connect(self._browse)
        program_row.addWidget(self.browse_button)
        layout.addLayout(program_row)

        flags_label = QLabel("Launch options (advanced, usually leave as they are):")
        flags_label.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(flags_label)
        self.flags_edit = QLineEdit(" ".join(row.pre_args))
        self.flags_edit.setPlaceholderText("for example -fullscreen")
        layout.addWidget(self.flags_edit)

        if row.shares_package_with:
            shared = QLabel(f"This also changes {', '.join(row.shares_package_with)}, which iiSU launches the same way.")
            shared.setWordWrap(True)
            shared.setStyleSheet(f"color: {TEXT_DIM};")
            layout.addWidget(shared)

        self.error_label = QLabel("")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._initial_label = row.emulator
        self._initial_exe_names = list(row.exe_names)
        self.combo.currentIndexChanged.connect(self._on_choice_changed)
        self._on_choice_changed(initial=True)

    def _choice(self) -> dict | None:
        index = self.combo.currentIndex()
        return self._choices[index] if 0 <= index < len(self._choices) else None

    def _on_choice_changed(self, *_args, initial: bool = False) -> None:
        choice = self._choice()
        is_other = choice is None
        self.browse_button.setVisible(is_other)
        if is_other:
            if self._program_path is not None:
                self.program_label.setText(str(self._program_path))
            elif initial and self._initial_exe_names:
                self.program_label.setText(f"Currently: {', '.join(self._initial_exe_names)}")
            else:
                self.program_label.setText("Pick the emulator's program file.")
        else:
            self.program_label.setText(f"Looks for {', '.join(choice['exe_names'])} in your emulator folders.")
            if not initial:
                self.flags_edit.setText(" ".join(choice["pre_args"]))

    def _browse(self) -> None:
        file_filter = "Programs (*.exe)" if sys.platform == "win32" else "All files (*)"
        chosen, _ = QFileDialog.getOpenFileName(self, "Pick the emulator's program", "", file_filter)
        if chosen:
            self._program_path = Path(chosen)
            self.program_label.setText(chosen)

    def _on_accept(self) -> None:
        flags = self.flags_edit.text().split()
        choice = self._choice()
        if choice is not None:
            self.result_values = (list(choice["exe_names"]), flags, bool(choice.get("rom_before_args")), None)
        elif self._program_path is not None:
            self.result_values = ([self._program_path.name], flags, False, self._program_path.parent)
        elif self._initial_exe_names and self.combo.findText(self._initial_label) < 0:
            # A custom program kept as it was, with only its options edited.
            self.result_values = (self._initial_exe_names, flags, False, None)
        else:
            self.error_label.setText("Pick the program with Browse first.")
            return
        self.accept()
