"""Builds and installs a stub app for each of shared/emulator_defaults.py's
known packages into the running AVD, so iiSU's own installed-package check
resolves each console to something our patched LaunchBridge recognizes
(see that module's docstring for why a stub is needed at all). The AVD
needs to already be running, Start it from Home first if this can't
reach it.

Replaces bridge/emulator_dialogs.py's tkinter RedirectorInstallDialog.
"""

import subprocess

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
from shared.emulator_defaults import all_stub_packages
from shared.qt_theme import Fonts, SPACING_MD, SPACING_SM

import stub_apk


class RedirectorInstallDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install Redirector Apps")
        self.resize(560, 420)
        self.running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        description = QLabel(
            "Installs a placeholder app for each of the emulators below into the "
            "running AVD, purely so iiSU recognizes that console's emulator as "
            "installed. The real launch is still handled by the PC-side emulator "
            "configured in the Emulators tab, these apps do nothing themselves."
        )
        description.setWordWrap(True)
        description.setProperty("role", "dim")
        layout.addWidget(description)

        self.replace_checkbox = QCheckBox(
            "Replace apps already installed under these package names\n"
            "(only do this if you're sure it's an old redirector, not a real app)"
        )
        layout.addWidget(self.replace_checkbox)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        layout.addWidget(self.log_text, stretch=1)

        self._log_stream = LogStreamRedirector()
        self._log_stream.text_written.connect(self._append_log)

        button_row = QHBoxLayout()
        self.install_button = QPushButton("Install All")
        self.install_button.setObjectName("accent")
        self.install_button.clicked.connect(self._start_install)
        button_row.addWidget(self.install_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        button_row.addStretch()
        layout.addLayout(button_row)

    def _append_log(self, text: str) -> None:
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)

    def _start_install(self) -> None:
        if self.running:
            return
        self.running = True
        self.install_button.setEnabled(False)
        self._install_signals = run_in_background(
            self._run_installs, self._on_finished, self._on_finished, self.replace_checkbox.isChecked()
        )

    def _run_installs(self, replace_existing: bool) -> None:
        devices = subprocess.run(["adb", "devices"], capture_output=True, text=True, creationflags=0x08000000)  # CREATE_NO_WINDOW
        if not any(line.startswith("emulator-") and "device" in line for line in devices.stdout.splitlines()):
            self._log_stream.write("No running AVD found, start it from Home first, then try again.\n")
            return

        for package, label in all_stub_packages():
            self._log_stream.write(f"{label} ({package})... ")
            try:
                outcome = stub_apk.build_and_install(package, label, replace_existing=replace_existing)
                self._log_stream.write(f"{outcome}\n")
            except Exception as e:  # noqa: BLE001; reported in the dialog's own log either way
                self._log_stream.write(f"failed ({e})\n")
        self._log_stream.write("\nDone.\n")

    def _on_finished(self, _result=None) -> None:
        self.running = False
        self.install_button.setEnabled(True)
