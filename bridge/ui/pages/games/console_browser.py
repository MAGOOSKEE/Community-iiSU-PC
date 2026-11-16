"""Console Games page. Backed entirely by sync_library.py (already non-GUI and tested) and
console_names.py, no new service module needed, this page just wires
those straight to a tree."""

from pathlib import Path, PurePosixPath

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

import game_overrides
import sync_library
from bridge_config import save_config
from console_names import load_console_lookup
from bridge.ui.dialogs.game_settings_dialog import GameSettingsDialog
from bridge.ui.pages.base import PageBase
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import RED, TEXT_DIM

GAMES_CONSOLE_ALL_SYSTEMS = "All Systems"


def _disc_separation_label(row: dict) -> str:
    """The Disc Separation column: "Separate" for a playlist whose discs
    are kept as their own entries, "Combined" for one that syncs as a
    single entry, and nothing for a plain file, which has no discs to
    combine."""
    if row["excepted"]:
        return "Separate"
    return "Combined" if row["is_playlist"] else ""


class ConsoleBrowserPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window
        self._rows: list[dict] = []

        self.add_header(
            "Console Games",
            "Every game in your ROM library, grouped as it syncs to the VM. A multi-disc "
            "game backed by an .m3u/.cue shows as one entry.",
        )

        search_row = QWidget()
        search_row_layout = QHBoxLayout(search_row)
        search_row_layout.setContentsMargins(0, 0, 0, 0)
        search_row_layout.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._render_filtered)
        search_row_layout.addWidget(self.search_edit, 1)
        search_row_layout.addWidget(QLabel("System:"))
        self.system_combo = QComboBox()
        self.system_combo.addItems([GAMES_CONSOLE_ALL_SYSTEMS])
        self.system_combo.currentTextChanged.connect(self._render_filtered)
        search_row_layout.addWidget(self.system_combo)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {TEXT_DIM};")
        search_row_layout.addWidget(self.count_label)
        self.body_layout.addWidget(search_row)

        self.status_label = QLabel("Open this page to scan your ROM library.")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.status_label)

        action_row = QWidget()
        action_row_layout = QHBoxLayout(action_row)
        action_row_layout.setContentsMargins(0, 0, 0, 0)
        rescan_button = QPushButton("Rescan")
        rescan_button.setObjectName("accent")
        rescan_button.clicked.connect(self.refresh)
        action_row_layout.addWidget(rescan_button)
        # Always on screen and greyed out until they apply, rather than
        # appearing when rows are selected, so the row of buttons never
        # jumps. The same actions are in the right-click menu.
        self.settings_button = QPushButton("Game Settings...")
        self.settings_button.setObjectName("ghost")
        self.settings_button.setToolTip("Use another emulator, flags or a program before launch, for the selected game.")
        self.settings_button.clicked.connect(lambda: self._edit_game_settings(self._selected_rows()[0]))
        action_row_layout.addWidget(self.settings_button)
        self.keep_button = QPushButton("Keep Discs Separate")
        self.keep_button.setObjectName("ghost")
        self.keep_button.setToolTip("Shows each disc of the selected playlist in iiSU as its own game.")
        self.keep_button.clicked.connect(self._add_exceptions)
        action_row_layout.addWidget(self.keep_button)
        self.merge_button = QPushButton("Merge Discs Together")
        self.merge_button.setObjectName("ghost")
        self.merge_button.setToolTip("Shows the selected playlist in iiSU as one game again.")
        self.merge_button.clicked.connect(self._remove_exceptions)
        action_row_layout.addWidget(self.merge_button)
        action_row_layout.addStretch(1)
        self.selection_hint = QLabel("")
        self.selection_hint.setStyleSheet(f"color: {TEXT_DIM};")
        action_row_layout.addWidget(self.selection_hint)
        self.body_layout.addWidget(action_row)

        note = QLabel(
            "A multi-disc game shows in iiSU as one entry. Keep Discs Separate is for the rare "
            "playlist whose discs are really separate games."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(note)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Console", "Type", "File", "Disc Separation"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 160)
        self.tree.setColumnWidth(3, 260)
        self.tree.itemSelectionChanged.connect(self._update_selection_hint)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.body_layout.addWidget(self.tree, 1)

        self._scan_signals = None
        self._update_selection_hint()

    def _on_double_click(self, *_args) -> None:
        if self.settings_button.isEnabled():
            self._edit_game_settings(self._selected_rows()[0])

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        raw = self.window.config_data.get("roms_dir", "")
        if not raw or not Path(raw).is_dir():
            self._apply_scan([], "Set your ROM directory first.", True)
            return
        self.status_label.setText("Scanning...")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self._scan_signals = run_in_background(self._scan_worker, self._apply_scan_result, None, Path(raw))

    def _scan_worker(self, roms_dir: Path):
        try:
            exact, by_compact = load_console_lookup()
            exceptions = sync_library.load_dedupe_exceptions()
            consoles, skipped = sync_library.scan_library(roms_dir, exact, by_compact, dedupe_exceptions=exceptions)
        except OSError as e:
            return [], str(e), True

        consoles.pop("windows", None)
        rows = []
        seen_keys = set()
        for shortname, entries in consoles.items():
            for rel, _size, _mtime in entries:
                exception_key = f"{shortname}/{rel}"
                seen_keys.add(exception_key)
                rows.append(
                    {
                        "shortname": shortname,
                        "rel": rel,
                        "name": PurePosixPath(rel).stem,
                        "is_playlist": PurePosixPath(rel).suffix.lower() in (".m3u", ".cue"),
                        "exception_key": exception_key,
                        "excepted": exception_key in exceptions,
                        "hidden_from_iisu": False,
                    }
                )

        for exception_key in exceptions:
            if exception_key in seen_keys or "/" not in exception_key:
                continue
            shortname, _sep, rel = exception_key.partition("/")
            rows.append(
                {
                    "shortname": shortname,
                    "rel": rel,
                    "name": PurePosixPath(rel).stem,
                    "is_playlist": True,
                    "exception_key": exception_key,
                    "excepted": True,
                    "hidden_from_iisu": True,
                }
            )

        rows.sort(key=lambda r: (r["shortname"], r["name"].casefold()))
        game_count = len({(r["shortname"], r["name"]) for r in rows if not r["hidden_from_iisu"]})
        status = f"{game_count} game(s) across {len(consoles)} console(s)"
        if skipped:
            status += f", {len(skipped)} folder(s) not recognized as a console"
        return rows, status, False

    def _apply_scan_result(self, result) -> None:
        rows, status, error = result
        self._apply_scan(rows, status, error)

    def _apply_scan(self, rows: list[dict], status: str, error: bool) -> None:
        self._rows = rows
        self.status_label.setText(status)
        self.status_label.setStyleSheet(f"color: {RED if error else TEXT_DIM};")
        current = self.system_combo.currentText()
        systems = [GAMES_CONSOLE_ALL_SYSTEMS] + sorted({row["shortname"] for row in rows})
        self.system_combo.blockSignals(True)
        self.system_combo.clear()
        self.system_combo.addItems(systems)
        self.system_combo.setCurrentText(current if current in systems else GAMES_CONSOLE_ALL_SYSTEMS)
        self.system_combo.blockSignals(False)
        self._render_filtered()

    def _render_filtered(self, *_args) -> None:
        self.tree.clear()
        query = self.search_edit.text().strip().casefold()
        system_filter = self.system_combo.currentText() or GAMES_CONSOLE_ALL_SYSTEMS
        for row in self._rows:
            if system_filter != GAMES_CONSOLE_ALL_SYSTEMS and row["shortname"] != system_filter:
                continue
            haystack = f"{row['name']} {row['shortname']} {row['rel']}".casefold()
            if query and query not in haystack:
                continue
            if row["hidden_from_iisu"]:
                type_label = "Playlist (hidden from iiSU)"
            elif row["is_playlist"]:
                type_label = "Playlist/Sheet"
            else:
                type_label = "File"
            has_override = game_overrides.get_override(self.window.config_data, PurePosixPath(row["rel"]).name) is not None
            item = QTreeWidgetItem([
                f"{row['name']}  (custom settings)" if has_override else row["name"],
                row["shortname"], type_label, row["rel"], _disc_separation_label(row),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, row["exception_key"])
            if row["hidden_from_iisu"]:
                dim = QColor(TEXT_DIM)
                for col in range(5):
                    item.setForeground(col, dim)
            self.tree.addTopLevelItem(item)

        shown = self.tree.topLevelItemCount()
        total = len(self._rows)
        filtered = bool(query) or system_filter != GAMES_CONSOLE_ALL_SYSTEMS
        self.count_label.setText(f"{shown} shown / {total} total" if filtered else f"{total} entries")

    def _selected_rows(self) -> list[dict]:
        selected_keys = {item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()}
        return [row for row in self._rows if row["exception_key"] in selected_keys]

    def _update_selection_hint(self) -> None:
        """Enables the action buttons that apply to the selection."""
        selected = self._selected_rows()
        self.settings_button.setEnabled(len(selected) == 1 and not selected[0]["hidden_from_iisu"])
        self.keep_button.setEnabled(any(row["is_playlist"] for row in selected))
        self.merge_button.setEnabled(any(row["excepted"] for row in selected))
        self.selection_hint.setText(f"{len(selected)} selected" if selected else "Select a game for its settings.")


    def _show_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        # Right-clicking an item outside the current selection replaces
        # it, matching how Explorer/most list UIs treat a right-click,
        # right-clicking *inside* an existing multi-selection acts on the
        # whole selection instead of collapsing it to just the one row.
        if item not in self.tree.selectedItems():
            self.tree.setCurrentItem(item)
        selected = self._selected_rows()
        if not selected:
            return

        menu = QMenu(self)
        settings_action = menu.addAction("Game Settings...")
        settings_action.setEnabled(len(selected) == 1 and not selected[0]["hidden_from_iisu"])
        menu.addSeparator()
        keep_action = menu.addAction("Keep Discs Separate")
        keep_action.setEnabled(any(row["is_playlist"] for row in selected))
        merge_action = menu.addAction("Merge Discs Together")
        merge_action.setEnabled(any(row["excepted"] for row in selected))
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == settings_action:
            self._edit_game_settings(selected[0])
        elif chosen == keep_action:
            self._add_exceptions()
        elif chosen == merge_action:
            self._remove_exceptions()

    def _edit_game_settings(self, row: dict) -> None:
        # The bridge only ever sees the ROM's file name at launch, so that's
        # the key; it's the last part of the library-relative path.
        rom_filename = PurePosixPath(row["rel"]).name
        dialog = GameSettingsDialog(rom_filename, self.window.config_data, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.result_override is None:
            return
        updated = game_overrides.set_override(self.window.config_data, rom_filename, dialog.result_override)
        try:
            save_config(updated)
        except OSError as exc:
            QMessageBox.critical(self, "Game Settings", f"Couldn't save the setting:\n\n{exc}")
            return
        self.window.config_data = updated
        self._render_filtered()

    def _add_exceptions(self) -> None:
        selected = [row for row in self._selected_rows() if row["is_playlist"]]
        if not selected:
            QMessageBox.information(self, "Console Games", "Select one or more playlist (.m3u) or sheet (.cue) entries first.")
            return
        exceptions = sync_library.load_dedupe_exceptions()
        exceptions.update(row["exception_key"] for row in selected)
        sync_library.save_dedupe_exceptions(exceptions)
        self.refresh()

    def _remove_exceptions(self) -> None:
        selected = [row for row in self._selected_rows() if row["excepted"]]
        if not selected:
            QMessageBox.information(self, "Console Games", 'Select one or more entries whose Disc Separation is "Separate" first.')
            return
        exceptions = sync_library.load_dedupe_exceptions()
        exceptions.difference_update(row["exception_key"] for row in selected)
        sync_library.save_dedupe_exceptions(exceptions)
        self.refresh()
