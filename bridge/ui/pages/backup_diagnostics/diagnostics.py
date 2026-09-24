"""Diagnostics page -- ports manager.py's _build_diagnostics_page. Backed
by bridge/services/diagnostics_service.py."""

import os

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bridge.services import diagnostics_service as svc
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, TEXT_DIM
from bridge_config import save_config


class DiagnosticsPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window

        self.add_header(
            "Diagnostics", "Run non-destructive checks for iiSU-PC, the Android VM, bridge, Windows Apps, Steam, and logs."
        )

        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        run_button = QPushButton("Run Diagnostics")
        run_button.setObjectName("accent")
        run_button.clicked.connect(self._run_diagnostics)
        toolbar_layout.addWidget(run_button)
        manager_log_button = QPushButton("Open Manager Log")
        manager_log_button.setObjectName("ghost")
        manager_log_button.clicked.connect(lambda: self._open_path(svc.MANAGER_LOG_PATH))
        toolbar_layout.addWidget(manager_log_button)
        bridge_log_button = QPushButton("Open Bridge Log")
        bridge_log_button.setObjectName("ghost")
        bridge_log_button.clicked.connect(lambda: self._open_path(svc.BRIDGE_DIR / "bridge_debug.log"))
        toolbar_layout.addWidget(bridge_log_button)
        toolbar_layout.addStretch(1)
        self.body_layout.addWidget(toolbar)

        update_card = Card()
        update_layout = QVBoxLayout(update_card)
        update_top = QHBoxLayout()
        update_top.addWidget(QLabel("Community-iiSU-PC Updates"))
        update_top.addStretch(1)
        self.auto_updates_check = QCheckBox("Automatically apply updates on startup")
        self.auto_updates_check.setChecked(bool(window.config_data.get("auto_updates", False)))
        self.auto_updates_check.toggled.connect(self._set_auto_updates)
        update_top.addWidget(self.auto_updates_check)
        update_layout.addLayout(update_top)

        update_note = QLabel(
            "Off is recommended for customized installations. When enabled, startup may "
            "fast-forward a Git checkout or apply a newer release over tracked project files."
        )
        update_note.setWordWrap(True)
        update_note.setStyleSheet(f"color: {TEXT_DIM};")
        update_layout.addWidget(update_note)

        update_actions = QHBoxLayout()
        check_button = QPushButton("Check for Updates Now")
        check_button.setObjectName("ghost")
        check_button.clicked.connect(self._check_for_updates_now)
        update_actions.addWidget(check_button)
        self.update_status_label = QLabel("This check is read-only: it never downloads or installs an update.")
        self.update_status_label.setStyleSheet(f"color: {TEXT_DIM};")
        update_actions.addWidget(self.update_status_label, 1)
        update_layout.addLayout(update_actions)
        self.body_layout.addWidget(update_card)

        self.summary_label = QLabel("Diagnostics have not been run yet.")
        self.summary_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.summary_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Status", "Check", "Details"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 210)
        self.body_layout.addWidget(self.tree, 1)

        self._update_check_inflight = False
        self._diag_signals = None
        self._update_signals = None

    def _open_path(self, path) -> None:
        try:
            if not path.exists():
                QMessageBox.warning(self, "Diagnostics", f"Not found:\n{path}")
                return
            os.startfile(str(path))
        except Exception as exc:
            QMessageBox.critical(self, "Diagnostics", f"Couldn't open:\n{path}\n\n{exc}")

    def _set_auto_updates(self, enabled: bool) -> None:
        self.window.config_data["auto_updates"] = enabled
        try:
            save_config(self.window.config_data)
        except Exception as exc:
            self.auto_updates_check.blockSignals(True)
            self.auto_updates_check.setChecked(not enabled)
            self.auto_updates_check.blockSignals(False)
            self.window.config_data["auto_updates"] = not enabled
            QMessageBox.critical(self, "Community-iiSU-PC Updates", f"Couldn't save the update setting:\n\n{exc}")
            return
        state = "enabled" if enabled else "disabled"
        self.update_status_label.setText(f"Automatic startup updates are {state}. Check for Updates Now remains read-only.")

    def _check_for_updates_now(self) -> None:
        if self._update_check_inflight:
            return
        self._update_check_inflight = True
        self.update_status_label.setText("Checking for updates (read-only)...")
        self._update_signals = run_in_background(svc.check_for_updates, self._apply_update_check, self._update_check_error)

    def _apply_update_check(self, message: str) -> None:
        self._update_check_inflight = False
        self.update_status_label.setText(message)

    def _update_check_error(self, message: str) -> None:
        self._update_check_inflight = False
        self.update_status_label.setText(f"Update check failed: {message}")

    def _run_diagnostics(self) -> None:
        self.tree.clear()
        self.summary_label.setText("Running diagnostics...")
        self._diag_signals = run_in_background(svc.run_diagnostics, self._apply_diagnostics, None, self.window.config_data)

    def _apply_diagnostics(self, results: list) -> None:
        for status, check, details in results:
            self.tree.addTopLevelItem(QTreeWidgetItem([status, check, details]))
        self.summary_label.setText(svc.summarize(results))
