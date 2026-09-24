"""Browse locally-installed Steam games -- replaces manager.py's
_import_steam_library() Toplevel. Used both for bulk import (multi_select)
and, from WindowsAppDialog, to pick a single game when adding/editing a
Steam Game mapping (multi_select=False)."""

from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QDialog,
)

from bridge.services import windows_apps_service as svc
from shared.qt_theme import Fonts, TEXT_DIM


class SteamLibraryDialog(QDialog):
    def __init__(self, parent=None, multi_select: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Import Steam Library" if multi_select else "Choose an installed Steam game")
        self.resize(760, 560)
        self.chosen: list[tuple[str, str]] = []

        self._games = svc.installed_steam_games()
        self._already = svc.steam_ids_already_added(svc.load_windows_apps())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)

        title = QLabel("Import Steam Library" if multi_select else "Choose an installed Steam game")
        title.setFont(Fonts.heading())
        layout.addWidget(title)

        libs = svc.steam_library_paths()
        summary = QLabel(f"Found {len(self._games)} installed game(s) across {len(libs)} Steam library folder(s).")
        summary.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(summary)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.textChanged.connect(self._refill)
        search_row.addWidget(self.filter_edit, 1)
        layout.addLayout(search_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Game", "App ID", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(
            QTreeWidget.SelectionMode.ExtendedSelection if multi_select else QTreeWidget.SelectionMode.SingleSelection
        )
        self.tree.setColumnWidth(0, 420)
        layout.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import Selected" if multi_select else "Choose")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refill()

    def _refill(self) -> None:
        selected_ids = set()
        for item in self.tree.selectedItems():
            selected_ids.add(item.text(1))
        self.tree.clear()
        query = self.filter_edit.text().strip().casefold()
        for game in self._games:
            if query and query not in game["name"].casefold() and query not in game["appid"]:
                continue
            state = "Already in iiSU" if game["appid"] in self._already else "Installed"
            item = QTreeWidgetItem([game["name"], game["appid"], state])
            self.tree.addTopLevelItem(item)
            if game["appid"] in selected_ids and game["appid"] not in self._already:
                item.setSelected(True)

    def _on_accept(self) -> None:
        self.chosen = [
            (item.text(0), item.text(1)) for item in self.tree.selectedItems() if item.text(2) != "Already in iiSU"
        ]
        self.accept()
