"""Android Storage page, browse/transfer files on the Android VM's
shared storage over ADB. Ports manager.py's _build_android_storage_page
and its supporting methods. Backed by
bridge/services/android_storage_service.py."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from bridge.services import android_storage_service as svc
from bridge.ui.dialogs.android_text_editor_dialog import AndroidTextEditorDialog
from bridge.ui.pages.base import PageBase
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import RED, TEXT_DIM


class AndroidStoragePage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window
        self._rows: list[tuple[str, bool, int]] = []
        self._last_status = ""

        self.add_header("Android Storage", "Browse and transfer files directly between Windows and the iiSU Android VM.")

        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        up_button = QPushButton("Up")
        up_button.setObjectName("ghost")
        up_button.clicked.connect(self._go_up)
        top_layout.addWidget(up_button)
        self.path_edit = QLineEdit("/storage/emulated/0")
        self.path_edit.returnPressed.connect(self.refresh)
        top_layout.addWidget(self.path_edit, 1)
        go_button = QPushButton("Go")
        go_button.setObjectName("ghost")
        go_button.clicked.connect(self.refresh)
        top_layout.addWidget(go_button)
        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("ghost")
        refresh_button.clicked.connect(self.refresh)
        top_layout.addWidget(refresh_button)
        self.body_layout.addWidget(top)

        search_row = QWidget()
        search_row_layout = QHBoxLayout(search_row)
        search_row_layout.setContentsMargins(0, 0, 0, 0)
        search_row_layout.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._render_filtered)
        search_row_layout.addWidget(self.search_edit, 1)
        self.body_layout.addWidget(search_row)

        self.status_label = QLabel("Open this page while the Android VM is running.")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.status_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type", "Size"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 470)
        self.tree.itemDoubleClicked.connect(self._open_selected)
        self.body_layout.addWidget(self.tree, 1)

        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        for label, handler in (
            ("Upload File...", self._upload_file),
            ("Upload Folder...", self._upload_folder),
            ("Download...", self._download),
            ("Edit Text...", self._edit_text),
            ("New Folder...", self._new_folder),
            ("Rename...", self._rename),
            ("Delete", self._delete),
        ):
            button = QPushButton(label)
            button.setObjectName("ghost")
            button.clicked.connect(handler)
            buttons_layout.addWidget(button)
        buttons_layout.addStretch(1)
        self.body_layout.addWidget(buttons)

        self._refresh_signals = None
        self._action_signals = None

    def on_shown(self) -> None:
        self.refresh()

    # == Listing ==

    def refresh(self) -> None:
        path = self.path_edit.text().strip() or "/storage/emulated/0"
        if not path.startswith("/"):
            path = "/" + path
        self.path_edit.setText(path)
        self.status_label.setText("Loading...")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self._refresh_signals = run_in_background(svc.list_directory, self._apply_listing, None, path)

    def _apply_listing(self, result) -> None:
        rows, status = result
        if rows is None:
            self.tree.clear()
            self.status_label.setText(status)
            self.status_label.setStyleSheet(f"color: {RED};")
            return
        self._rows = rows
        self._last_status = status
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self._render_filtered()

    def _render_filtered(self, *_args) -> None:
        self.tree.clear()
        query = self.search_edit.text().strip().casefold()
        for name, is_dir, size in self._rows:
            if query and query not in name.casefold():
                continue
            item = QTreeWidgetItem([name, "Folder" if is_dir else "File", "" if is_dir else svc.format_size(size)])
            self.tree.addTopLevelItem(item)
        shown = self.tree.topLevelItemCount()
        total = len(self._rows)
        suffix = f"{shown} of {total} item(s)" if query else f"{total} item(s)"
        self.status_label.setText(f"{self._last_status} • {suffix}" if self._last_status else suffix)

    def _selected(self) -> list[tuple[str, bool]]:
        return [(item.text(0), item.text(1) == "Folder") for item in self.tree.selectedItems()]

    def _open_selected(self, *_args) -> None:
        selected = self._selected()
        if len(selected) != 1 or not selected[0][1]:
            return
        self.path_edit.setText(svc.android_join(self.path_edit.text(), selected[0][0]))
        self.refresh()

    def _go_up(self) -> None:
        self.path_edit.setText(svc.android_parent(self.path_edit.text()))
        self.refresh()

    # == Actions ==

    def _run_async(self, description: str, fn, *args) -> None:
        self.status_label.setText(description + "...")
        self._action_signals = run_in_background(fn, self._on_action_done, self._on_action_failed, *args)

    def _on_action_done(self, _result) -> None:
        self.refresh()

    def _on_action_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Android Storage", message)
        self.refresh()

    def _upload_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Upload file to Android")
        if path:
            self._run_async("Uploading file", svc.upload_paths, self.path_edit.text(), [path])

    def _upload_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Upload folder to Android")
        if path:
            self._run_async("Uploading folder", svc.upload_paths, self.path_edit.text(), [path])

    def _download(self) -> None:
        selected = self._selected()
        if not selected:
            QMessageBox.information(self, "Android Storage", "Select one or more files/folders first.")
            return
        dest = QFileDialog.getExistingDirectory(self, "Download selected items to...")
        if not dest:
            return
        names = [name for name, _is_dir in selected]
        self._run_async("Downloading", svc.download_paths, self.path_edit.text(), names, dest)

    def _edit_text(self) -> None:
        selected = self._selected()
        if len(selected) != 1 or selected[0][1]:
            QMessageBox.information(self, "Android Storage", "Select exactly one text file to edit.")
            return
        name = selected[0][0]
        remote = svc.android_join(self.path_edit.text(), name)
        try:
            content = svc.read_text_file(remote)
        except svc.AndroidStorageServiceError as e:
            QMessageBox.critical(self, "Android Storage", str(e))
            return
        dialog = AndroidTextEditorDialog(self, remote, content)
        dialog.exec()

    def _new_folder(self) -> None:
        name = self._prompt("New Folder", "Folder name:")
        if not name:
            return
        remote = svc.android_join(self.path_edit.text(), name)
        self._run_async("Creating folder", svc.make_directory, remote)

    def _rename(self) -> None:
        selected = self._selected()
        if len(selected) != 1:
            QMessageBox.information(self, "Android Storage", "Select exactly one item to rename.")
            return
        old_name, is_dir = selected[0]
        new_name = self._prompt("Rename", "New name:", old_name)
        if not new_name or new_name == old_name:
            return
        if not is_dir:
            from pathlib import Path

            old_suffix = Path(old_name).suffix
            if old_suffix and not Path(new_name).suffix:
                new_name += old_suffix
        base = self.path_edit.text()
        self._run_async("Renaming", svc.rename, svc.android_join(base, old_name), svc.android_join(base, new_name))

    def _delete(self) -> None:
        selected = self._selected()
        if not selected:
            QMessageBox.information(self, "Android Storage", "Select one or more items first.")
            return
        names = ", ".join(name for name, _ in selected[:5])
        if len(selected) > 5:
            names += f" and {len(selected) - 5} more"
        reply = QMessageBox.question(self, "Delete from Android?", f"Permanently delete {names} from the VM?\n\nThis cannot be undone.")
        if reply != QMessageBox.StandardButton.Yes:
            return
        base = self.path_edit.text()
        self._delete_many(base, selected)

    def _delete_many(self, base: str, selected: list[tuple[str, bool]]) -> None:
        def work():
            for name, is_dir in selected:
                svc.delete(svc.android_join(base, name), is_dir)

        self.status_label.setText("Deleting...")
        self._action_signals = run_in_background(work, self._on_action_done, self._on_action_failed)

    def _prompt(self, title: str, label: str, initial: str = "") -> str | None:
        while True:
            text, ok = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, initial)
            if not ok:
                return None
            value = text.strip()
            if not value or "/" in value or value in {".", ".."}:
                QMessageBox.critical(self, title, "Enter a valid single file/folder name.")
                initial = text
                continue
            return value
