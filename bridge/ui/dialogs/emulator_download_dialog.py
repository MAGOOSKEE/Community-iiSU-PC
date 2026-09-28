"""Dialog for selecting and downloading supported PC emulators, via
Flatpak (Flathub, user mode) on Linux or Winget on Windows, see
bridge/emulator_downloader.py."""

from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from emulator_downloader import detect_backend, get_catalog_with_status, install_emulator

from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, GREEN, RED, SPACING_MD, SPACING_SM, TEXT_DIM


class EmulatorDownloadDialog(QDialog):
    download_finished = Signal(bool)  # True if anything was actually installed

    def __init__(self, parent=None, search_roots: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Download Emulators")
        self.resize(700, 540)
        self.running = False
        self.installed_any = False
        self._search_roots = [Path(root) for root in (search_roots or []) if Path(root).is_dir()]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        self.backend, self.backend_info = detect_backend()
        if self.backend == "flatpak":
            backend_desc = "Downloads and installs emulators via Flatpak (Flathub user mode, no root password required)."
        elif self.backend == "winget":
            backend_desc = "Downloads and installs emulators via Windows Package Manager (winget)."
        else:
            backend_desc = self.backend_info

        description = QLabel(
            "Select the main emulators you want Community-iiSU-PC to download and install.\n" + backend_desc
        )
        description.setWordWrap(True)
        description.setProperty("role", "dim")
        layout.addWidget(description)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Emulator", "Systems / Consoles", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 340)
        self.tree.setColumnWidth(2, 110)
        layout.addWidget(self.tree, stretch=1)

        selection_row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.clicked.connect(self._select_all)
        selection_row.addWidget(select_all)
        select_none = QPushButton("Select None")
        select_none.clicked.connect(self._select_none)
        selection_row.addWidget(select_none)
        select_missing = QPushButton("Select Missing")
        select_missing.clicked.connect(self._select_missing)
        selection_row.addWidget(select_missing)
        selection_row.addStretch(1)
        layout.addLayout(selection_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(self.status_label)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        self.log_text.setMaximumHeight(140)
        layout.addWidget(self.log_text)

        self._log_stream = LogStreamRedirector()
        self._log_stream.text_written.connect(self._append_log)

        button_row = QHBoxLayout()
        self.download_button = QPushButton("Download Selected")
        self.download_button.setObjectName("accent")
        self.download_button.clicked.connect(self._start_download)
        self.download_button.setEnabled(self.backend != "none")
        button_row.addWidget(self.download_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        button_row.addWidget(self.close_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._load_catalog()

    def _load_catalog(self) -> None:
        self.tree.clear()
        for emu in get_catalog_with_status(self._search_roots):
            item = QTreeWidgetItem([emu["name"], emu["systems"], "Installed" if emu["installed"] else "Not installed"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked if emu["installed"] else Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, emu)
            item.setForeground(2, Qt.GlobalColor.green if emu["installed"] else Qt.GlobalColor.gray)
            self.tree.addTopLevelItem(item)

    def _select_all(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked)

    def _select_none(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)

    def _select_missing(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            emu = item.data(0, Qt.ItemDataRole.UserRole)
            item.setCheckState(0, Qt.CheckState.Unchecked if emu.get("installed") else Qt.CheckState.Checked)

    def _append_log(self, text: str) -> None:
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)

    def _selected_emulators(self) -> list[dict]:
        return [
            self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked
        ]

    def _start_download(self) -> None:
        if self.running:
            return
        selected = self._selected_emulators()
        if not selected:
            self.status_label.setText("Select at least one emulator to download.")
            self.status_label.setStyleSheet(f"color: {RED};")
            return

        self.running = True
        self.download_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.tree.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(selected))
        self.progress_bar.setValue(0)
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.status_label.setText(f"Starting installation of {len(selected)} emulator(s)...")

        self._signals = run_in_background(self._run_downloads, self._on_finished, self._on_error, selected=selected)

    def _run_downloads(self, selected: list[dict]) -> tuple[int, int]:
        success_count = 0
        total = len(selected)
        for index, emu in enumerate(selected, 1):
            self._log_stream.write(f"\n{'=' * 40}\n[{index}/{total}] Installing {emu['name']} ({emu['systems']})...\n{'=' * 40}\n")
            ok, error = install_emulator(emu, on_output=self._log_stream.write)
            if ok:
                success_count += 1
                self.installed_any = True
                self._log_stream.write(f"Installed {emu['name']} successfully.\n")
            else:
                self._log_stream.write(f"Failed to install {emu['name']}: {error}\n")
        return success_count, total

    def _on_finished(self, result: tuple[int, int]) -> None:
        success_count, total = result
        self.running = False
        self.download_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self.tree.setEnabled(True)
        self.progress_bar.setValue(total)

        if success_count == total:
            self.status_label.setText(f"Successfully installed all {total} selected emulator(s)!")
            self.status_label.setStyleSheet(f"color: {GREEN};")
        else:
            self.status_label.setText(f"Installed {success_count} of {total} emulator(s). Check the log for details.")
            self.status_label.setStyleSheet(f"color: {RED if success_count == 0 else GREEN};")

        self._load_catalog()
        self.download_finished.emit(self.installed_any)

    def _on_error(self, message: str) -> None:
        self.running = False
        self.download_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self.tree.setEnabled(True)
        self.status_label.setText(f"Download failed: {message}")
        self.status_label.setStyleSheet(f"color: {RED};")
        self._load_catalog()
