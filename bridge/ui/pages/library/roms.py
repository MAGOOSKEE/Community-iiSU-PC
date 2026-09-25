"""ROM Directory settings page, ports manager.py's _build_roms_page and
its supporting methods (~lines 996-1070). Widgets persist across saves
instead of being torn down and rebuilt (reload_from_config() just resets
their values), since Qt has no need for Tk's destroy-and-rebuild-on-every-
config-change pattern."""

from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QWidget,
)

from console_names import load_console_lookup, resolve_console_shortname
from bridge.ui.pages.base import PageBase
from shared.qt_theme import GREEN, RED, TEXT_DIM


class RomsPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window

        self.add_header(
            "ROM Directory",
            "Where your games live, and where Community-iiSU-PC looks for your PC emulators.",
        )

        self.body_layout.addWidget(QLabel("Root ROM folder (contains one subfolder per console):"))
        roms_row = QWidget()
        roms_row_layout = QHBoxLayout(roms_row)
        roms_row_layout.setContentsMargins(0, 0, 0, 0)
        self.roms_dir_edit = QLineEdit(window.config_data.get("roms_dir", ""))
        self.roms_dir_edit.textChanged.connect(self._refresh_roms_status)
        roms_row_layout.addWidget(self.roms_dir_edit, 1)
        browse_button = QPushButton("Browse...")
        browse_button.setObjectName("ghost")
        browse_button.clicked.connect(self._browse_roms_dir)
        roms_row_layout.addWidget(browse_button)
        self.body_layout.addWidget(roms_row)

        self.roms_status_label = QLabel("")
        self.roms_status_label.setWordWrap(True)
        self.body_layout.addWidget(self.roms_status_label)

        self.body_layout.addWidget(QLabel("Folders to search for emulator executables:"))
        self.search_roots_list = QListWidget()
        self.search_roots_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.body_layout.addWidget(self.search_roots_list, 1)

        btn_row = QWidget()
        btn_row_layout = QHBoxLayout(btn_row)
        btn_row_layout.setContentsMargins(0, 0, 0, 0)
        add_button = QPushButton("Add folder...")
        add_button.setObjectName("ghost")
        add_button.clicked.connect(self._add_search_root)
        btn_row_layout.addWidget(add_button)
        remove_button = QPushButton("Remove selected")
        remove_button.setObjectName("ghost")
        remove_button.clicked.connect(self._remove_search_root)
        btn_row_layout.addWidget(remove_button)
        btn_row_layout.addStretch(1)
        self.body_layout.addWidget(btn_row)

        self.reload_from_config()

    def reload_from_config(self) -> None:
        self.roms_dir_edit.setText(self.window.config_data.get("roms_dir", ""))
        self.search_roots_list.clear()
        self.search_roots_list.addItems(self.window.config_data.get("search_roots", []))
        self._refresh_roms_status()

    def get_roms_dir(self) -> str:
        return self.roms_dir_edit.text().strip()

    def get_search_roots(self) -> list[str]:
        return [self.search_roots_list.item(i).text() for i in range(self.search_roots_list.count())]

    def _refresh_roms_status(self) -> None:
        raw = self.roms_dir_edit.text().strip()
        if not raw:
            self.roms_status_label.setText("")
            return
        path = Path(raw)
        if not path.is_dir():
            self.roms_status_label.setText("This folder doesn't exist yet.")
            self.roms_status_label.setStyleSheet(f"color: {RED};")
            return

        exact, by_compact = load_console_lookup()
        recognized, unrecognized = [], []
        for child in sorted(path.iterdir()):
            if not child.is_dir():
                continue
            (recognized if resolve_console_shortname(child.name, exact, by_compact) else unrecognized).append(child.name)

        if not recognized and not unrecognized:
            self.roms_status_label.setText("This folder is empty.")
            self.roms_status_label.setStyleSheet(f"color: {TEXT_DIM};")
        elif not unrecognized:
            self.roms_status_label.setText(f"iiSU will recognize all {len(recognized)} folder(s): {', '.join(recognized)}")
            self.roms_status_label.setStyleSheet(f"color: {GREEN};")
        else:
            prefix = f"{len(recognized)} recognized, " if recognized else ""
            self.roms_status_label.setText(f"{prefix}{len(unrecognized)} won't be seen by iiSU (rename these): {', '.join(unrecognized)}")
            self.roms_status_label.setStyleSheet(f"color: {RED};")

    def _browse_roms_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select root ROM folder")
        if path:
            self.roms_dir_edit.setText(path)

    def _add_search_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select a folder to search for emulators")
        if path:
            self.search_roots_list.addItem(path)

    def _remove_search_root(self) -> None:
        for item in self.search_roots_list.selectedItems():
            self.search_roots_list.takeItem(self.search_roots_list.row(item))
