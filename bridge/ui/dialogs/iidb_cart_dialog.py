"""iiDB Cart, review/remove staged assets and trigger Install All.
Replaces manager.py's _iidb_open_cart() Toplevel."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from bridge.services import iidb_service as svc
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, TEXT_DIM

_KEY_ROLE = Qt.ItemDataRole.UserRole


class IidbCartDialog(QDialog):
    def __init__(self, parent, cart: dict, on_cart_changed):
        super().__init__(parent)
        self._cart = cart
        self._on_cart_changed = on_cart_changed
        self.setWindowTitle(f"iiDB Cart ({len(cart)})")
        self.resize(880, 500)

        layout = QVBoxLayout(self)
        title = QLabel("iiDB Cart")
        title.setFont(Fonts.title())
        layout.addWidget(title)
        note = QLabel("Install All downloads originals to the durable Media Library, then installs them through MediaBridge.")
        note.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(note)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Game", "Category", "Asset ID", "Resolution / Duration"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 190)
        self.tree.setColumnWidth(1, 130)
        self.tree.itemSelectionChanged.connect(self._update_selection_hint)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree, 1)
        self._refill()

        row = QHBoxLayout()
        clear_button = QPushButton("Clear Cart")
        clear_button.setObjectName("ghost")
        clear_button.clicked.connect(self._clear_all)
        row.addWidget(clear_button)
        row.addStretch(1)
        # Remove Selected lives only in the right-click menu -- same
        # convention as Console Games/Emulators/Android Storage.
        self.selection_hint = QLabel("")
        self.selection_hint.setStyleSheet(f"color: {TEXT_DIM};")
        row.addWidget(self.selection_hint)
        close_button = QPushButton("Close")
        close_button.setObjectName("ghost")
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)
        self.install_button = QPushButton("Install All")
        self.install_button.setObjectName("accent")
        self.install_button.clicked.connect(self._install_all)
        row.addWidget(self.install_button)
        layout.addLayout(row)

        self._install_signals = None
        self._update_selection_hint()

    def _update_selection_hint(self) -> None:
        count = len(self.tree.selectedItems())
        if count:
            self.selection_hint.setText(f"{count} selected, right-click to remove.")
        else:
            self.selection_hint.setText("Select item(s), then right-click to remove.")

    def _show_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        if item not in self.tree.selectedItems():
            self.tree.setCurrentItem(item)
        menu = QMenu(self)
        remove_action = menu.addAction("Remove Selected")
        if menu.exec(self.tree.viewport().mapToGlobal(pos)) == remove_action:
            self._remove_selected()

    def _refill(self) -> None:
        self.tree.clear()
        for key, item in self._cart.items():
            asset = item["asset"]
            detail = asset.get("resolution") or (f"{asset.get('width')}×{asset.get('height')}" if asset.get("width") and asset.get("height") else "")
            if asset.get("duration_ms"):
                detail = (detail + " • " if detail else "") + f"{float(asset['duration_ms']) / 1000:.1f}s"
            tree_item = QTreeWidgetItem([item["game_name"], svc.type_label(asset.get("type", "")), str(asset.get("id", asset.get("asset_id", ""))), detail])
            tree_item.setData(0, _KEY_ROLE, key)
            self.tree.addTopLevelItem(tree_item)

    def _remove_selected(self) -> None:
        for item in self.tree.selectedItems():
            self._cart.pop(item.data(0, _KEY_ROLE), None)
        self._refill()
        self._on_cart_changed()

    def _clear_all(self) -> None:
        self._cart.clear()
        self._refill()
        self._on_cart_changed()

    def _install_all(self) -> None:
        try:
            plan = svc.build_install_plan(self._cart)
        except svc.IidbServiceError as e:
            QMessageBox.critical(self, "Install iiDB Media", str(e))
            return

        from bridge.services import android_storage_service, media_library_service

        ready, detail = android_storage_service.adb_device_ready()
        if not ready:
            QMessageBox.warning(self, "Install iiDB Media", "Start the Android VM before installing iiDB media.\n\n" + detail)
            return
        ping_ok, ping_detail = media_library_service.mediabridge_ping()
        if not ping_ok:
            QMessageBox.critical(self, "Install iiDB Media", "MediaBridge V1 is not ready. Repatch iiSU first.\n\n" + ping_detail)
            return

        summary = "\n".join(
            f"• {p['target']['display_name']}: {svc.type_label(p['item']['asset'].get('type', ''))} → {p['logical_type']} slot {p['slot']}"
            for p in plan
        )
        reply = QMessageBox.question(
            self,
            "Install iiDB Media",
            "Install these iiDB originals into iiSU?\n\n" + summary
            + "\n\nThe originals will also be saved permanently in the iiDB Media Library for recovery.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.install_button.setEnabled(False)
        self._install_signals = run_in_background(svc.install_plan, self._on_install_done, None, plan)

    def _on_install_done(self, result) -> None:
        installed, failures, rescan_ok, rescan_detail = result
        for p, _record in installed:
            self._cart.pop(p["key"], None)
        self._refill()
        self._on_cart_changed()
        self.install_button.setEnabled(True)

        warnings = []
        if failures:
            details = "\n".join(f"• {p['target']['display_name']} / {svc.type_label(p['item']['asset'].get('type', ''))}: {err}" for p, err in failures[:10])
            warnings.append(f"Asset install failures:\n{details}")
        if rescan_ok is False:
            warnings.append(
                "The media files were installed and registered, but iiSU's automatic Full Library Rescan did not start. "
                "The successful installs were kept.\n\n" + (rescan_detail or "No MediaBridge rescan detail was returned.")
            )
        if warnings:
            QMessageBox.warning(self, "Install iiDB Media", f"Installed: {len(installed)}\nFailed: {len(failures)}\n\n" + "\n\n".join(warnings))
        else:
            QMessageBox.information(
                self,
                "Install iiDB Media",
                f"Install complete.\n\nInstalled: {len(installed)}\nFailed: 0\n\niiSU's Full Library Rescan was started automatically.\n\n"
                "The installed originals are registered for Check / Restore Missing / Restore All.",
            )
        if not failures:
            self.accept()
