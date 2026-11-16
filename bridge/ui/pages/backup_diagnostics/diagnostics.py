"""Diagnostics page. Backed by bridge/services/diagnostics_service.py.

Problems come first, each with a button that does something about it (opens
the page that fixes it, repairs the settings, copies an install command).
The checks that passed are folded away under "Show passing checks", since a
list of 20 green rows buries the one that matters. Copy Report puts the
whole result on the clipboard for a bug report, with this PC's user folder
and account name taken out."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from bridge.services import diagnostics_service as svc
from shared.platform_compat import open_uri
from bridge.ui.pages.base import PageBase
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import AMBER, GREEN, RED, TEXT_DIM

STATUS_TEXT = {"OK": "OK", "WARNING": "Check", "ERROR": "Problem"}
STATUS_COLORS = {"OK": GREEN, "WARNING": AMBER, "ERROR": RED}

# Where each "open a page" fix goes: (top-level page, sub-page).
FIX_PAGES = {
    "roms": ("library", "roms"),
    "emulators": ("settings", "emulators"),
    "storage": ("settings", "storage"),
    "pc_games": ("games", "pc_games"),
    "home": ("home", None),
    "setup": ("home", None),
}


class DiagnosticsPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window
        self._results: list = []

        self.add_header("Diagnostics", "Checks your install without changing anything, and offers a fix for what it finds.")

        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.run_button = QPushButton("Run Diagnostics")
        self.run_button.setObjectName("accent")
        self.run_button.clicked.connect(self._run_diagnostics)
        toolbar_layout.addWidget(self.run_button)
        self.copy_button = QPushButton("Copy Report")
        self.copy_button.setObjectName("ghost")
        self.copy_button.setToolTip(
            "Copies the results for a bug report. Your user folder and account name are taken out."
        )
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_report)
        toolbar_layout.addWidget(self.copy_button)
        logs_button = QPushButton("Open Logs Folder")
        logs_button.setObjectName("ghost")
        logs_button.clicked.connect(lambda: self._open_path(svc.BRIDGE_DIR))
        toolbar_layout.addWidget(logs_button)
        toolbar_layout.addStretch(1)
        self.body_layout.addWidget(toolbar)

        self.summary_label = QLabel("Run Diagnostics to check your install.")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.summary_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", "Check", "Details", ""])
        self.tree.setRootIsDecorated(False)
        self.tree.setWordWrap(True)
        self.tree.setStyleSheet("QTreeWidget::item { min-height: 34px; }")
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.body_layout.addWidget(self.tree, 1)

        self.show_passing = QPushButton("Show passing checks")
        self.show_passing.setObjectName("ghost")
        self.show_passing.setCheckable(True)
        self.show_passing.toggled.connect(lambda _shown: self._render())
        self.show_passing.setVisible(False)
        self.body_layout.addWidget(self.show_passing, 0, Qt.AlignmentFlag.AlignLeft)

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
        self.run_button.setEnabled(False)
        self.summary_label.setText("Checking...")
        self._diag_signals = run_in_background(svc.run_diagnostics, self._apply_diagnostics, self._on_failed, self.window.config_data)

    def _on_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.summary_label.setText(f"Diagnostics stopped with an error: {message}")

    def _apply_diagnostics(self, results: list) -> None:
        self.run_button.setEnabled(True)
        self._results = svc.sort_problems_first(results)
        self.copy_button.setEnabled(True)
        problems = [row for row in self._results if row[0] != "OK"]
        passing = len(self._results) - len(problems)
        if problems:
            self.summary_label.setText(
                f"{len(problems)} thing{'s' if len(problems) != 1 else ''} to look at, and {passing} checks passed."
            )
        else:
            self.summary_label.setText(f"Everything looks fine: all {passing} checks passed.")
        self.show_passing.setVisible(bool(passing))
        self._render()

    def _render(self) -> None:
        self.tree.clear()
        shown_passing = self.show_passing.isChecked()
        passing = sum(1 for row in self._results if row[0] == "OK")
        self.show_passing.setText(
            "Hide passing checks" if shown_passing else f"Show passing checks ({passing})"
        )
        for status, check, details in self._results:
            if status == "OK" and not shown_passing:
                continue
            item = QTreeWidgetItem([STATUS_TEXT.get(status, status), check, details, ""])
            item.setForeground(0, QColor(STATUS_COLORS.get(status, TEXT_DIM)))
            item.setToolTip(2, details)
            self.tree.addTopLevelItem(item)
            fix = svc.fix_for(status, check)
            if fix is not None:
                action, label = fix
                button = QPushButton(label)
                button.setObjectName("RowButton")
                button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                button.clicked.connect(lambda _=False, a=action: self._do_fix(a))
                host = QWidget()
                host_layout = QHBoxLayout(host)
                host_layout.setContentsMargins(0, 2, 4, 2)
                host_layout.addWidget(button)
                host.setMinimumWidth(button.sizeHint().width() + 12)
                self.tree.setItemWidget(item, 3, host)

    def _do_fix(self, action: str) -> None:
        if action.startswith("copy:"):
            QGuiApplication.clipboard().setText(action.removeprefix("copy:"))
            self.summary_label.setText("Copied the install command. Paste it into a terminal.")
        elif action == "repair_config":
            self.window.offer_config_recovery()
        elif action == "launch_log":
            self._open_path(svc.BRIDGE_DIR / "launch_history.log")
        elif action in FIX_PAGES:
            page, sub_page = FIX_PAGES[action]
            self.window.open_page(page, sub_page)

    def _copy_report(self) -> None:
        QGuiApplication.clipboard().setText(svc.report_text(self._results))
        self.summary_label.setText(
            "Copied. Paste it into your bug report; your user folder and account name were taken out."
        )
