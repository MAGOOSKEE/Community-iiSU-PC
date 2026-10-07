"""Backup & Restore page, ports manager.py's _build_backup_restore_page.
Backed by bridge/services/backup_service.py."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from bridge.services import android_backup_service as android_svc
from bridge.services import backup_service as svc
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, TEXT_DIM


class BackupRestorePage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header("Backup & Restore", "Create a portable backup of iiSU-PC configuration, or restore one later.")

        card = Card()
        card_layout = QVBoxLayout(card)
        heading = QLabel("Configuration Backup")
        heading.setFont(Fonts.heading())
        card_layout.addWidget(heading)
        note = QLabel(
            "Backs up detected iiSU-PC configuration such as Windows app mappings "
            "and bridge settings. ROMs, Android VM storage, caches, logs, executables, "
            "and Steam game files are intentionally excluded."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_DIM};")
        card_layout.addWidget(note)

        button_row = QHBoxLayout()
        create_button = QPushButton("Create Backup...")
        create_button.setObjectName("accent")
        create_button.clicked.connect(self._create_backup)
        button_row.addWidget(create_button)
        restore_button = QPushButton("Restore Backup...")
        restore_button.setObjectName("ghost")
        restore_button.clicked.connect(self._restore_backup)
        button_row.addWidget(restore_button)
        button_row.addStretch(1)
        card_layout.addLayout(button_row)
        self.body_layout.addWidget(card)

        data_card = Card()
        data_layout = QVBoxLayout(data_card)
        data_heading = QLabel("iiSU Data (inside the Android VM)")
        data_heading.setFont(Fonts.heading())
        data_layout.addWidget(data_heading)
        data_note = QLabel(
            "Backs up iiSU's own media and app-data folders from the running Android VM, so a reinstall "
            "doesn't lose your custom artwork and settings. The VM has to be running. Restoring "
            "replaces files with the same names, leaves everything else alone, and stops iiSU while it "
            "copies; Stop and Open Community-iiSU-PC afterwards so iiSU reloads it. ROMs and the "
            "Media Bridge inbox are not included."
        )
        data_note.setWordWrap(True)
        data_note.setStyleSheet(f"color: {TEXT_DIM};")
        data_layout.addWidget(data_note)
        data_buttons = QHBoxLayout()
        self.data_backup_button = QPushButton("Back Up iiSU Data...")
        self.data_backup_button.setObjectName("accent")
        self.data_backup_button.clicked.connect(self._backup_android_data)
        data_buttons.addWidget(self.data_backup_button)
        self.data_restore_button = QPushButton("Restore iiSU Data...")
        self.data_restore_button.setObjectName("ghost")
        self.data_restore_button.clicked.connect(self._restore_android_data)
        data_buttons.addWidget(self.data_restore_button)
        data_buttons.addStretch(1)
        data_layout.addLayout(data_buttons)
        self.body_layout.addWidget(data_card)
        self._android_signals = None

        self.status_label = QLabel("Ready. Restore always creates a safety copy of files it replaces.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.status_label)
        self.body_layout.addStretch(1)

    def _create_backup(self) -> None:
        files = svc.backup_candidates()
        if not files:
            QMessageBox.warning(self, "Backup & Restore", "No supported iiSU-PC configuration files were found to back up.")
            return

        path, _filter = QFileDialog.getSaveFileName(
            self, "Create iiSU-PC Backup", svc.default_backup_filename(), "iiSU-PC Backup (*.zip);;ZIP archive (*.zip)"
        )
        if not path:
            return

        try:
            svc.create_backup(Path(path), files)
        except svc.BackupServiceError as e:
            QMessageBox.critical(self, "Backup & Restore", str(e))
            return

        self.status_label.setText(f"Backup created: {path} ({len(files)} configuration file(s))")
        QMessageBox.information(self, "Backup Complete", f"Backed up {len(files)} configuration file(s).\n\n{path}")

    def _restore_backup(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Restore iiSU-PC Backup", filter="iiSU-PC Backup (*.zip);;ZIP archive (*.zip)")
        if not path:
            return

        try:
            payloads = svc.inspect_backup(Path(path))
        except svc.BackupServiceError as e:
            QMessageBox.critical(self, "Backup & Restore", str(e))
            return

        shown = "\n".join(f"• {name}" for name in payloads)
        reply = QMessageBox.question(
            self,
            "Restore Backup",
            f"Restore these configuration files?\n\n{shown}\n\n"
            "Existing files will be copied to a timestamped safety folder first.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        restored, safety_count, safety_dir = svc.apply_restore(payloads)
        self.status_label.setText(f"Restored {restored} file(s). Safety copies: {safety_count}.")
        QMessageBox.information(
            self,
            "Restore Complete",
            f"Restored {restored} configuration file(s).\n\n"
            f"Safety copies created: {safety_count}\n"
            + (f"{safety_dir}\n\n" if safety_count else "\n")
            + "Restart the Manager/bridge before relying on restored settings.",
        )

    # == iiSU data inside the VM ==

    def _set_android_busy(self, busy: bool, message: str = "") -> None:
        self.data_backup_button.setEnabled(not busy)
        self.data_restore_button.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

    def _backup_android_data(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Back Up iiSU Data", android_svc.default_backup_filename(), "iiSU data backup (*.zip);;ZIP archive (*.zip)"
        )
        if not path:
            return
        self._set_android_busy(True, "Backing up iiSU data from the VM, this can take a while for a big library...")
        self._android_signals = run_in_background(
            android_svc.create_backup, lambda result: self._on_android_backup_done(path, result), self._on_android_error, Path(path)
        )

    def _on_android_backup_done(self, path: str, result) -> None:
        self._set_android_busy(False, f"iiSU data backed up: {path} ({result.summary()})")
        QMessageBox.information(self, "Backup Complete", f"{result.summary()}\n\n{path}")

    def _restore_android_data(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Restore iiSU Data", filter="iiSU data backup (*.zip);;ZIP archive (*.zip)")
        if not path:
            return
        try:
            found = android_svc.inspect_archive(Path(path))
        except android_svc.AndroidBackupError as e:
            QMessageBox.critical(self, "Backup & Restore", str(e))
            return
        shown = "\n".join(f"- {label}: {len(files)} file(s) to {android_svc.SOURCES[label]}" for label, files in found.items())
        reply = QMessageBox.question(
            self,
            "Restore iiSU Data",
            f"Copy these back into the running Android VM?\n\n{shown}\n\n"
            "Files with the same names are replaced. iiSU will be stopped while this runs.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_android_busy(True, "Restoring iiSU data into the VM...")
        self._android_signals = run_in_background(android_svc.restore_backup, self._on_android_restore_done, self._on_android_error, Path(path))

    def _on_android_restore_done(self, outcomes: dict) -> None:
        summary = "; ".join(f"{label}: {outcome}" for label, outcome in outcomes.items())
        self._set_android_busy(False, f"iiSU data restore finished ({summary}). Stop and Open Community-iiSU-PC to reload it.")
        QMessageBox.information(self, "Restore Complete", f"{summary}\n\nStop and Open Community-iiSU-PC so iiSU reloads its data.")

    def _on_android_error(self, message: str) -> None:
        self._set_android_busy(False, f"iiSU data backup/restore failed: {message}")
        QMessageBox.critical(self, "Backup & Restore", message)
