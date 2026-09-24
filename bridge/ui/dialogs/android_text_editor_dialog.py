"""Edit a small text file living on the Android VM in place -- replaces
manager.py's _android_storage_edit_text() Toplevel."""

from pathlib import Path

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout

from bridge.services import android_storage_service as svc
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, TEXT_DIM


class AndroidTextEditorDialog(QDialog):
    def __init__(self, parent, remote: str, content: str):
        super().__init__(parent)
        self._remote = remote
        self.setWindowTitle(f"Edit Text - {Path(remote).name}")
        self.resize(820, 600)

        layout = QVBoxLayout(self)
        path_label = QLabel(remote)
        path_label.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(path_label)

        self.text_edit = QPlainTextEdit(content)
        self.text_edit.setFont(Fonts.mono())
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.text_edit, 1)

        row = QHBoxLayout()
        self.status_label = QLabel("UTF-8 text editor")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        row.addWidget(self.status_label)
        row.addStretch(1)
        save_button = QPushButton("Save")
        save_button.setObjectName("accent")
        save_button.clicked.connect(self._save)
        row.addWidget(save_button)
        close_button = QPushButton("Close")
        close_button.setObjectName("ghost")
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)
        layout.addLayout(row)

        QShortcut(QKeySequence("Ctrl+S"), self, self._save)
        self._save_signals = None

    def _save(self) -> None:
        data = self.text_edit.toPlainText()
        self.status_label.setText("Saving...")
        suffix = Path(self._remote).suffix
        self._save_signals = run_in_background(svc.write_text_file, self._on_saved, self._on_save_failed, self._remote, data, suffix)

    def _on_saved(self, _result) -> None:
        self.status_label.setText("Saved")

    def _on_save_failed(self, message: str) -> None:
        self.status_label.setText("Save failed")
        QMessageBox.critical(self, "Android Storage", message)
