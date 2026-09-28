"""Diagnostics page, ports manager.py's _build_diagnostics_page. Backed
by bridge/services/diagnostics_service.py."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from bridge.services import diagnostics_service as svc
from shared.platform_compat import open_uri
from bridge.ui.pages.base import PageBase
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import TEXT_DIM


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

        self.summary_label = QLabel("Diagnostics have not been run yet.")
        self.summary_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.summary_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Status", "Check", "Details"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 210)
        self.body_layout.addWidget(self.tree, 1)

        self._diag_signals = None

    def _open_path(self, path) -> None:
        try:
            if not path.exists():
                QMessageBox.warning(self, "Diagnostics", f"Not found:\n{path}")
                return
            open_uri(str(path))
        except Exception as exc:
            QMessageBox.critical(self, "Diagnostics", f"Couldn't open:\n{path}\n\n{exc}")

    def _run_diagnostics(self) -> None:
        self.tree.clear()
        self.summary_label.setText("Running diagnostics...")
        self._diag_signals = run_in_background(svc.run_diagnostics, self._apply_diagnostics, None, self.window.config_data)

    def _apply_diagnostics(self, results: list) -> None:
        for status, check, details in results:
            self.tree.addTopLevelItem(QTreeWidgetItem([status, check, details]))
        self.summary_label.setText(svc.summarize(results))
