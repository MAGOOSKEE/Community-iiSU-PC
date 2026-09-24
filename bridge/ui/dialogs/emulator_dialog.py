"""Add/edit a single package-prefix -> emulator mapping. Replaces
bridge/emulator_dialogs.py's tkinter EmulatorDialog(simpledialog.Dialog)."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout

from shared.qt_theme import SPACING_MD, SPACING_SM


class EmulatorDialog(QDialog):
    def __init__(self, parent=None, title: str = "", prefix: str = "", exe_names: str = "", pre_args: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.result_values: tuple[str, list[str], list[str]] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        layout.addWidget(QLabel("Android package prefix (e.g. com.github.stenzek.duckstation)"))
        self.prefix_entry = QLineEdit(prefix)
        self.prefix_entry.setMinimumWidth(420)
        layout.addWidget(self.prefix_entry)

        layout.addWidget(QLabel("Executable name(s), comma-separated (e.g. retroarch.exe)"))
        self.exe_entry = QLineEdit(exe_names)
        layout.addWidget(self.exe_entry)

        layout.addWidget(QLabel("Launch flags, comma-separated (e.g. -fullscreen)"))
        self.args_entry = QLineEdit(pre_args)
        layout.addWidget(self.args_entry)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.prefix_entry.setFocus()

    def _on_accept(self) -> None:
        prefix = self.prefix_entry.text().strip()
        exe_names = [s.strip() for s in self.exe_entry.text().split(",") if s.strip()]
        pre_args = [s.strip() for s in self.args_entry.text().split(",") if s.strip()]
        self.result_values = (prefix, exe_names, pre_args)
        self.accept()
