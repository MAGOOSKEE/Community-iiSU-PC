"""Browse iiDB Media, search, preview, collect, and install iiDB media
through MediaBridge. Replaces manager.py's _iidb_open_browser() Toplevel.

Non-modal (shown via show(), not exec()) so it can stay open alongside
the rest of the Manager, matching the original's singleton Toplevel,
MediaLibraryPage holds one instance and re-raises it on a second click
instead of creating a duplicate.

Category filtering is a QComboBox here instead of the original's
regenerated row of pill buttons, same information, simpler to keep in
sync when a new game's categories are loaded."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bridge.services import iidb_service as svc
from bridge.services.media_library_service import human_size
from bridge.ui.audio import mci_playback
from bridge.ui.dialogs.iidb_cart_dialog import IidbCartDialog
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, TEXT_DIM

_RESULT_ROLE = Qt.ItemDataRole.UserRole


class IidbBrowserDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Browse iiDB Media")
        self.resize(1180, 760)
        self.setModal(False)

        self._search_results: list[dict] = []
        self._assets: list[dict] = []
        self._filtered_assets: list[dict] = []
        self._current_parent_id = None
        self._current_game: dict | None = None
        self._selected_asset: dict | None = None
        self._cart: dict = {}
        self._audio_alias: str | None = None
        self._preview_token = None

        layout = QVBoxLayout(self)

        title_row = QHBoxLayout()
        title_label = QLabel("Browse iiDB")
        title_label.setFont(Fonts.title())
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        self.cart_button = QPushButton("Cart (0)")
        self.cart_button.setObjectName("accent")
        self.cart_button.clicked.connect(self._open_cart)
        title_row.addWidget(self.cart_button)
        layout.addLayout(title_row)

        subtitle = QLabel("Search, preview, collect, and install iiDB media through MediaBridge.")
        subtitle.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(subtitle)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.returnPressed.connect(self._search)
        search_row.addWidget(self.search_edit, 1)
        search_button = QPushButton("Search")
        search_button.setObjectName("accent")
        search_button.clicked.connect(self._search)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)

        self.status_label = QLabel("Search for a game to begin. No VM connection is required.")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(self.status_label)

        body = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(body, 1)

        self.results_tree = QTreeWidget()
        self.results_tree.setHeaderLabels(["Game", "Platform / Details"])
        self.results_tree.setRootIsDecorated(False)
        self.results_tree.itemSelectionChanged.connect(self._on_result_selected)
        body.addWidget(self.results_tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.game_title_label = QLabel("Select a game")
        self.game_title_label.setFont(Fonts.heading())
        right_layout.addWidget(self.game_title_label)
        self.game_detail_label = QLabel("")
        self.game_detail_label.setStyleSheet(f"color: {TEXT_DIM};")
        right_layout.addWidget(self.game_detail_label)

        self.category_combo = QComboBox()
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        right_layout.addWidget(self.category_combo)

        lower = QSplitter(Qt.Orientation.Horizontal)
        right_layout.addWidget(lower, 1)

        self.assets_tree = QTreeWidget()
        self.assets_tree.setHeaderLabels(["Type", "Resolution", "Size", "Filename"])
        self.assets_tree.setRootIsDecorated(False)
        self.assets_tree.itemSelectionChanged.connect(self._on_asset_selected)
        lower.addWidget(self.assets_tree)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        self.preview_label = QLabel("Select an asset\nto preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(200)
        preview_layout.addWidget(self.preview_label, 1)
        self.preview_detail_label = QLabel("")
        self.preview_detail_label.setWordWrap(True)
        self.preview_detail_label.setStyleSheet(f"color: {TEXT_DIM};")
        preview_layout.addWidget(self.preview_detail_label)

        self.play_button = QPushButton("Play Soundbite")
        self.play_button.setObjectName("ghost")
        self.play_button.clicked.connect(self._play_selected_soundbite)
        preview_layout.addWidget(self.play_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("ghost")
        self.stop_button.clicked.connect(self._stop_audio)
        preview_layout.addWidget(self.stop_button)
        self.play_button.hide()
        self.stop_button.hide()

        self.cart_asset_button = QPushButton("Add to Cart")
        self.cart_asset_button.setObjectName("accent")
        self.cart_asset_button.clicked.connect(self._toggle_selected_cart)
        preview_layout.addWidget(self.cart_asset_button)

        lower.addWidget(preview_panel)
        lower.setSizes([430, 240])
        body.addWidget(right)
        body.setSizes([300, 700])

        self.search_edit.setFocus()

        self._search_signals = None
        self._parent_signals = None
        self._thumb_signals = None
        self._audio_signals = None

    def closeEvent(self, event) -> None:
        self._stop_audio()
        super().closeEvent(event)

    # == Search ==

    def _search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            return
        self.status_label.setText(f"Searching iiDB for {query!r}…")
        self.results_tree.clear()
        self._search_signals = run_in_background(svc.search_games, self._show_search_results, self._search_failed, query)

    def _search_failed(self, message: str) -> None:
        self.status_label.setText("iiDB search failed: " + message)

    def _show_search_results(self, results: list[dict]) -> None:
        self._search_results = results
        self.results_tree.clear()
        for row in results:
            item = QTreeWidgetItem([row["name"], row["subtitle"]])
            item.setData(0, _RESULT_ROLE, row["id"])
            self.results_tree.addTopLevelItem(item)
        self.status_label.setText(f"Found {len(results)} game result{'s' if len(results) != 1 else ''}." if results else "No game results found.")

    def _on_result_selected(self) -> None:
        items = self.results_tree.selectedItems()
        if not items:
            return
        index = self.results_tree.indexOfTopLevelItem(items[0])
        if index < 0 or index >= len(self._search_results):
            return
        row = self._search_results[index]
        self._stop_audio()
        self._current_parent_id = row["id"]
        self._current_game = row
        self._selected_asset = None
        self.game_title_label.setText(row["name"])
        self.game_detail_label.setText(f"iiDB parent ID: {row['id']} • Loading asset catalog…")
        self.status_label.setText(f"Loading {row['name']} metadata and previews…")
        self.assets_tree.clear()
        self.category_combo.clear()

        self._parent_signals = run_in_background(svc.load_parent_assets, lambda result: self._show_parent(row, result), self._parent_load_failed, row["id"])

    def _parent_load_failed(self, message: str) -> None:
        self.status_label.setText("Couldn't load iiDB game: " + message)

    def _show_parent(self, row: dict, result) -> None:
        if str(self._current_parent_id) != str(row["id"]):
            return
        landing, assets = result
        self._assets = assets
        counts = svc.category_counts(assets)
        total = svc.total_asset_count(landing, len(assets))
        self.game_detail_label.setText(f"iiDB parent ID: {row['id']} • {total} assets")

        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem(f"All ({len(assets)})", None)
        for category in svc.CATEGORY_ORDER:
            if counts.get(category):
                self.category_combo.addItem(f"{svc.type_label(category)} ({counts[category]})", category)
        self.category_combo.blockSignals(False)
        self._filter_assets(None)
        self.status_label.setText(f"Loaded {len(assets)} asset records for {row['name']}. Select a category or asset to preview.")

    def _on_category_changed(self, index: int) -> None:
        self._filter_assets(self.category_combo.itemData(index))

    def _filter_assets(self, asset_type: str | None) -> None:
        self._filtered_assets = [a for a in self._assets if asset_type is None or str(a.get("type", "")).lower() == asset_type]
        self.assets_tree.clear()
        for asset in self._filtered_assets:
            resolution = asset.get("resolution") or (f"{asset.get('width')}×{asset.get('height')}" if asset.get("width") and asset.get("height") else "")
            item = QTreeWidgetItem([svc.type_label(asset.get("type", "")), resolution, human_size(asset.get("size")), asset.get("filename") or str(asset.get("id") or "")])
            self.assets_tree.addTopLevelItem(item)

    # == Asset preview ==

    def _on_asset_selected(self) -> None:
        items = self.assets_tree.selectedItems()
        if not items:
            return
        index = self.assets_tree.indexOfTopLevelItem(items[0])
        if index < 0 or index >= len(self._filtered_assets):
            return
        asset = self._filtered_assets[index]
        self._stop_audio()
        self._selected_asset = asset

        aid = asset.get("id", asset.get("asset_id", "?"))
        label = svc.type_label(asset.get("type", ""))
        resolution = asset.get("resolution") or (f"{asset.get('width')}×{asset.get('height')}" if asset.get("width") and asset.get("height") else "")
        duration = asset.get("duration_ms")
        detail = f"{label}\nAsset ID: {aid}\n{resolution}\n{human_size(asset.get('size'))}"
        if duration:
            detail += f"\nDuration: {float(duration) / 1000:.1f}s"
        self.preview_detail_label.setText(detail.strip())
        self._refresh_cart_button()

        if str(asset.get("type", "")).lower() == "soundbite":
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Soundbite\n\nUse Play Soundbite below to preview audio.")
            self.play_button.show()
            self.stop_button.show()
            return

        self.play_button.hide()
        self.stop_button.hide()
        url = asset.get("preview_url") or asset.get("library_preview_url")
        if not url:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("No image preview\navailable")
            return

        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Loading preview…")
        token = (str(self._current_parent_id), str(aid), str(url))
        self._preview_token = token
        self._thumb_signals = run_in_background(svc.fetch_thumbnail, lambda path: self._set_preview(token, path), lambda msg: self._preview_failed(token, msg), url)

    def _set_preview(self, token, path: Path) -> None:
        if self._preview_token != token:
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.preview_label.setText("Preview unavailable")
            return
        scaled = pixmap.scaled(250, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

    def _preview_failed(self, token, message: str) -> None:
        if self._preview_token != token:
            return
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Preview unavailable")
        self.status_label.setText("Preview failed: " + message)

    # == Cart ==

    def _refresh_cart_button(self) -> None:
        if not self._selected_asset:
            return
        key = svc.cart_key(self._current_parent_id, self._selected_asset)
        self.cart_asset_button.setText("✓ In Cart (Remove)" if key in self._cart else "Add to Cart")

    def _toggle_selected_cart(self) -> None:
        if not self._selected_asset or not self._current_game:
            return
        key = svc.cart_key(self._current_parent_id, self._selected_asset)
        if key in self._cart:
            self._cart.pop(key, None)
        else:
            self._cart[key] = {
                "parent_id": self._current_game["id"],
                "game_name": self._current_game["name"],
                "game_subtitle": self._current_game.get("subtitle", ""),
                "asset": dict(self._selected_asset),
            }
        self.cart_button.setText(f"Cart ({len(self._cart)})")
        self._refresh_cart_button()

    def _open_cart(self) -> None:
        dialog = IidbCartDialog(self, self._cart, self._on_cart_changed_from_dialog)
        dialog.exec()

    def _on_cart_changed_from_dialog(self) -> None:
        self.cart_button.setText(f"Cart ({len(self._cart)})")
        self._refresh_cart_button()

    # == Soundbite preview ==

    def _play_selected_soundbite(self) -> None:
        asset = self._selected_asset
        if not asset or str(asset.get("type", "")).lower() != "soundbite":
            return
        url = asset.get("preview_url") or asset.get("raw_url") or asset.get("library_preview_url")
        if not url:
            self.status_label.setText("This soundbite has no playable URL.")
            return
        self._stop_audio()
        self.status_label.setText("Loading soundbite preview…")
        token = (str(self._current_parent_id), str(asset.get("id", asset.get("asset_id", "?"))), str(url))
        self._audio_signals = run_in_background(svc.fetch_soundbite_preview, lambda path: self._start_audio(token, path), lambda msg: self.status_label.setText("Soundbite preview failed: " + msg), url)

    def _start_audio(self, token, path: Path) -> None:
        try:
            self._audio_alias = mci_playback.play(path)
            self.status_label.setText("Playing soundbite preview. Use Stop to end playback.")
        except mci_playback.MciPlaybackError as exc:
            self.status_label.setText("Soundbite preview failed: " + str(exc))

    def _stop_audio(self) -> None:
        mci_playback.stop(self._audio_alias)
        self._audio_alias = None
