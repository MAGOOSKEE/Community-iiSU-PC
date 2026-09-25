"""Backup & Restore page, ports manager.py's _build_backup_restore_page.
Backed by bridge/services/backup_service.py."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from bridge.services import backup_service as svc
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
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
