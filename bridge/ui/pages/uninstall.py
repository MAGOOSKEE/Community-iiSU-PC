"""Uninstall page -- ports manager.py's _build_uninstall_page and its
supporting scan/confirm/run methods. Deliberately never locked behind
LOCKED_NAV (see sidebar.py) so it stays reachable against a partial or
broken install, same as the original."""

import sys
import traceback

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import uninstall as uninstall_cli
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.gradient_divider import GradientDivider
from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, INPUT_BG, LOG_TEXT, RED, TEXT_DIM


class UninstallPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window
        self._targets_cache: list = []

        title = QLabel("Uninstall")
        title.setFont(Fonts.title())
        title.setStyleSheet(f"color: {RED};")
        self.body_layout.addWidget(title)
        subtitle = QLabel(
            "Removes the Android VM, its SDK, your bridge config, the signing keystore, and\n"
            "the desktop shortcut. Does NOT touch your ROM library, your PC emulators, or the\n"
            "iiSU APK you supplied."
        )
        subtitle.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(subtitle)
        self.body_layout.addWidget(GradientDivider())

        list_card = Card()
        list_layout = QVBoxLayout(list_card)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Will remove", "Size"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 580)
        list_layout.addWidget(self.tree)
        self.body_layout.addWidget(list_card, 1)

        bottom_row = QWidget()
        bottom_layout = QHBoxLayout(bottom_row)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.total_label = QLabel("")
        self.total_label.setStyleSheet(f"color: {TEXT_DIM};")
        bottom_layout.addWidget(self.total_label)
        bottom_layout.addStretch(1)
        self.uninstall_button = QPushButton("Remove Everything")
        self.uninstall_button.setObjectName("accent")
        self.uninstall_button.clicked.connect(self._confirm_uninstall)
        bottom_layout.addWidget(self.uninstall_button)
        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("ghost")
        refresh_button.clicked.connect(self.refresh_preview)
        bottom_layout.addWidget(refresh_button)
        self.body_layout.addWidget(bottom_row)

        log_card = Card()
        log_layout = QVBoxLayout(log_card)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        self.log_text.setFixedHeight(120)
        self.log_text.setStyleSheet(f"background-color: {INPUT_BG}; color: {LOG_TEXT}; border: none;")
        log_layout.addWidget(self.log_text)
        self.body_layout.addWidget(log_card)

        self._log_redirector = LogStreamRedirector()
        self._log_redirector.text_written.connect(self._append_log)
        self._scan_signals = None
        self._run_signals = None

        self.refresh_preview()

    def on_shown(self) -> None:
        self.refresh_preview()

    def refresh_preview(self) -> None:
        self.total_label.setText("Scanning...")
        self.uninstall_button.setEnabled(False)
        self._scan_signals = run_in_background(self._scan_targets, self._apply_preview)

    def _scan_targets(self):
        avd_name = uninstall_cli.detect_avd_name()
        targets = uninstall_cli.collect_targets(avd_name)
        existing = [(p, uninstall_cli.dir_size(p)) for p in targets if p.exists()]
        return targets, existing

    def _apply_preview(self, result) -> None:
        targets, existing = result
        self._targets_cache = targets
        self.tree.clear()
        total = 0
        for path, size in existing:
            total += size
            if size >= 1e8:
                size_text = f"{size / 1e9:.2f} GB"
            elif size >= 1e3:
                size_text = f"{size / 1e6:.1f} MB"
            else:
                size_text = ""
            self.tree.addTopLevelItem(QTreeWidgetItem([str(path), size_text]))
        if not existing:
            self.total_label.setText("Nothing to remove. This already looks like a clean slate.")
            self.uninstall_button.setEnabled(False)
        else:
            self.total_label.setText(f"~{total / 1e9:.2f} GB will be reclaimed.")
            self.uninstall_button.setEnabled(True)

    def _confirm_uninstall(self) -> None:
        existing_count = sum(1 for p in self._targets_cache if p.exists())
        if existing_count == 0:
            return
        reply = QMessageBox.warning(
            self,
            "Remove everything?",
            f"This will permanently remove {existing_count} item(s) -- the Android VM, its SDK, "
            "your bridge config, the signing keystore, and the desktop shortcut.\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.uninstall_button.setEnabled(False)
        self._run_signals = run_in_background(self._run_uninstall, self._on_uninstall_finished)

    def _run_uninstall(self) -> None:
        old_stdout = sys.stdout
        sys.stdout = self._log_redirector
        try:
            print("[uninstall] stopping the AVD and bridge (if running)...")
            uninstall_cli.stop_running_instance()
            print("[uninstall] removing...")
            reclaimed = 0
            for path in self._targets_cache:
                if path.exists():
                    print(f"  removing {path}...")
                reclaimed += uninstall_cli.remove_path(path)
            print(f"\n=== Done -- reclaimed {reclaimed / 1e9:.1f} GB ===")
        except Exception:
            print(f"\n[uninstall] error:\n{traceback.format_exc()}")
        finally:
            sys.stdout = old_stdout

    def _on_uninstall_finished(self, _result) -> None:
        self.refresh_preview()

    def _append_log(self, text: str) -> None:
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.ensureCursorVisible()
