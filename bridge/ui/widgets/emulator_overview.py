"""The console-first list of emulators: which emulator each console opens,
whether it's installed, and a Download or Change button on the row itself.

Shared by the Manager's PC Emulators page and the onboarding wizard, so the
two can't drift apart. It owns no settings of its own: it reads the current
mappings, search folders and ROM folder through the callables it's given,
and hands a changed mapping back through set_emulators, so the page or
wizard that hosts it stays the one place that saves.
"""

from collections.abc import Callable

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,

)

from console_names import load_console_lookup, resolve_console_shortname
from bridge.services import emulator_overview_service as svc
from bridge.ui.dialogs.change_emulator_dialog import ChangeEmulatorDialog
from bridge.ui.dialogs.emulator_download_dialog import EmulatorDownloadDialog
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import GREEN, RED, TEXT_DIM

STATUS_COLORS = {svc.STATUS_FOUND: GREEN, svc.STATUS_BY_LINK: TEXT_DIM, svc.STATUS_MISSING: RED}


def resolve_folder(name: str) -> str | None:
    exact, by_compact = load_console_lookup()
    return resolve_console_shortname(name, exact, by_compact)


class EmulatorOverview(QWidget):
    # Emitted after a check finishes, with the rows (for a host that
    # summarizes them, such as the onboarding wizard's last page).
    checked = Signal(list)

    def __init__(
        self,
        get_emulators: Callable[[], dict],
        set_emulators: Callable[[dict], None],
        get_search_roots: Callable[[], list[str]],
        add_search_root: Callable[[str], None],
        get_roms_dir: Callable[[], str],
        parent=None,
    ):
        super().__init__(parent)
        self._get_emulators = get_emulators
        self._set_emulators = set_emulators
        self._get_search_roots = get_search_roots
        self._add_search_root = add_search_root
        self._get_roms_dir = get_roms_dir
        self.rows: list[svc.ConsoleRow] = []
        self._check_signals = None
        self._checking = False
        self._recheck_pending = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.summary_label = QLabel("Checking which emulators are installed...")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Console", "Your files", "Emulator", "Status", ""])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tree.setStyleSheet("QTreeWidget::item { min-height: 34px; }")
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.download_button = QPushButton("Download What's Missing...")
        self.download_button.setObjectName("accent")
        self.download_button.clicked.connect(self._download_missing)
        buttons.addWidget(self.download_button)
        self.recheck_button = QPushButton("Check Again")
        self.recheck_button.setObjectName("ghost")
        self.recheck_button.setToolTip("Looks for your emulators again, for example after installing one yourself.")
        self.recheck_button.clicked.connect(self.refresh)
        buttons.addWidget(self.recheck_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    # == Checking ==

    def refresh(self) -> None:
        """Rebuilds the rows from the current mappings and looks for each
        emulator in the background. A refresh asked for mid-check runs
        once the current one ends, so the list never shows stale rows."""
        if self._checking:
            self._recheck_pending = True
            return
        self._checking = True
        self.recheck_button.setEnabled(False)
        rows = svc.build_rows(self._get_emulators())
        self._render(rows, checking=True)
        self._check_signals = run_in_background(
            self._check, self._on_checked, self._on_check_failed,
            rows=rows, search_roots=list(self._get_search_roots()), roms_dir=self._get_roms_dir(),
        )

    @staticmethod
    def _check(rows, search_roots, roms_dir):
        svc.check_installed(rows, search_roots)
        svc.mark_library(rows, svc.library_consoles(roms_dir, resolve_folder))
        return svc.sort_for_display(rows)

    def _finish_check(self) -> None:
        self._checking = False
        self.recheck_button.setEnabled(True)
        if self._recheck_pending:
            self._recheck_pending = False
            self.refresh()

    def _on_checked(self, rows) -> None:
        self.rows = rows
        self._render(rows)
        self.summary_label.setText(svc.summary(rows))
        needed = svc.needed_downloads(rows) or svc.needed_downloads(rows, only_library=False)
        self.download_button.setEnabled(bool(needed))
        self.checked.emit(rows)
        self._finish_check()

    def _on_check_failed(self, message: str) -> None:
        self.summary_label.setText(f"Couldn't check your emulators: {message}")
        self._finish_check()

    # == Drawing ==

    def _render(self, rows: list[svc.ConsoleRow], checking: bool = False) -> None:
        self.tree.clear()
        for row in rows:
            files_text = str(row.library_files) if row.library_files else ""
            emulator_text = row.emulator if row.is_default else f"{row.emulator} (changed)"
            status_text = "Checking..." if checking and not row.launched_by_link else row.status_text
            item = QTreeWidgetItem([row.console_label, files_text, emulator_text, status_text, ""])
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            item.setForeground(1, QColor(TEXT_DIM))
            if not checking:
                item.setForeground(3, QColor(STATUS_COLORS.get(row.status, TEXT_DIM)))
            if row.executable:
                item.setToolTip(3, str(row.executable))
            if row.by_extension:
                item.setToolTip(2, "RetroArch picks a core for each file type. Change one game's emulator in Games > Console.")
            self.tree.addTopLevelItem(item)
            self.tree.setItemWidget(item, 4, self._row_buttons(row, checking))

    def _row_buttons(self, row: svc.ConsoleRow, checking: bool) -> QWidget:
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 2, 4, 2)
        layout.setSpacing(6)
        layout.addStretch(1)
        if not checking and row.status == svc.STATUS_MISSING and row.catalog_id:
            download = QPushButton("Download")
            download.setObjectName("RowButton")
            download.setToolTip(f"Installs {svc.catalog_name(row.catalog_id)}.")
            download.clicked.connect(lambda _=False, cid=row.catalog_id: self._open_downloader([cid]))
            download.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout.addWidget(download)
        if row.can_change:
            change = QPushButton("Change...")
            change.setObjectName("RowButton")

            change.setToolTip(f"Pick another emulator for {row.console_label}.")
            change.clicked.connect(lambda _=False, r=row: self._change(r))
            change.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout.addWidget(change)
        # Every row's buttons take the same width, so they line up.
        host.setMinimumWidth(190)
        return host

    # == Actions ==

    def _download_missing(self) -> None:
        needed = svc.needed_downloads(self.rows) or svc.needed_downloads(self.rows, only_library=False)
        self._open_downloader(needed)

    def _open_downloader(self, preselect: list[str]) -> None:
        dialog = EmulatorDownloadDialog(self, search_roots=self._get_search_roots(), preselect=preselect)
        dialog.exec()
        if dialog.installed_any:
            self.refresh()

    def _change(self, row: svc.ConsoleRow) -> None:
        dialog = ChangeEmulatorDialog(row, self)
        if dialog.exec() != ChangeEmulatorDialog.DialogCode.Accepted or dialog.result_values is None:
            return
        exe_names, pre_args, rom_before_args, folder = dialog.result_values
        if folder is not None and str(folder) not in self._get_search_roots():
            self._add_search_root(str(folder))
        self._set_emulators(svc.apply_choice(self._get_emulators(), row.package, exe_names, pre_args, rom_before_args))
        self.refresh()

    def missing_in_library(self) -> list[svc.ConsoleRow]:
        return [row for row in self.rows if row.in_library and row.status == svc.STATUS_MISSING]

