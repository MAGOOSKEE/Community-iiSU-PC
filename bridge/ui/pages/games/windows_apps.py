"""Windows Apps page, ports manager.py's _build_windows_apps_page and its
supporting methods. Backed entirely by bridge/services/windows_apps_service.py
(extracted first, see that module's docstring); this file is "wire a
service call to a signal/slot" per the Qt rewrite plan.

Not carried over from the Tk version, deliberately deferred rather than
half-built: drag-and-drop (Tk's own tkinterdnd2 path already degraded
gracefully without it), inline Steam search with fetched box-art
thumbnails (WindowsAppDialog uses a simple picker dialog instead), and the
Refresh Artwork feature. All cosmetic, none block using the page."""

import json
import os
import subprocess
from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bridge.services import windows_apps_service as svc
from bridge.ui.dialogs.steam_library_dialog import SteamLibraryDialog
from bridge.ui.dialogs.windows_app_dialog import WindowsAppDialog
from bridge.ui.dialogs.windows_apps_health_dialog import WindowsAppsHealthDialog
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, TEXT_DIM


class WindowsAppsPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window
        self._sort_column = "name"
        self._sort_reverse = False

        self.add_header("Windows Apps", "Native executables, Steam games, and registered Windows protocol links in iiSU.")

        steam_card = Card()
        steam_layout = QHBoxLayout(steam_card)
        steam_text_col = QWidget()
        steam_text_layout = QVBoxLayout(steam_text_col)
        steam_text_layout.setContentsMargins(0, 0, 0, 0)
        steam_heading = QLabel("Steam Library")
        steam_heading.setFont(Fonts.heading())
        steam_text_layout.addWidget(steam_heading)
        self.steam_summary_label = QLabel("Steam: scanning libraries...")
        self.steam_summary_label.setStyleSheet(f"color: {TEXT_DIM};")
        steam_text_layout.addWidget(self.steam_summary_label)
        steam_layout.addWidget(steam_text_col, 1)

        health_button = QPushButton("Health Check...")
        health_button.setObjectName("ghost")
        health_button.clicked.connect(self._open_health_check)
        steam_layout.addWidget(health_button)
        choose_button = QPushButton("Choose Games...")
        choose_button.setObjectName("ghost")
        choose_button.clicked.connect(self._import_steam_library)
        steam_layout.addWidget(choose_button)
        auto_import_button = QPushButton("Auto-import New")
        auto_import_button.setObjectName("accent")
        auto_import_button.clicked.connect(self._auto_import_new_steam_games)
        steam_layout.addWidget(auto_import_button)
        self.body_layout.addWidget(steam_card)

        search_row = QWidget()
        search_row_layout = QHBoxLayout(search_row)
        search_row_layout.setContentsMargins(0, 0, 0, 0)
        search_row_layout.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._refresh_tree)
        search_row_layout.addWidget(self.search_edit, 1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {TEXT_DIM};")
        search_row_layout.addWidget(self.count_label)
        self.body_layout.addWidget(search_row)

        hint = QLabel(
            "Steam import reads your installed Steam libraries locally. Steam artwork/box-art is not shown here."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(hint)

        action_row = QWidget()
        action_row_layout = QHBoxLayout(action_row)
        action_row_layout.setContentsMargins(0, 0, 0, 0)
        add_button = QPushButton("Add Application...")
        add_button.setObjectName("accent")
        add_button.clicked.connect(self._add_windows_app)
        action_row_layout.addWidget(add_button)
        more_button = QPushButton("More...")
        more_button.setObjectName("ghost")
        more_button.clicked.connect(lambda: self._show_more_menu(more_button))
        action_row_layout.addWidget(more_button)
        action_row_layout.addStretch(1)
        # Edit/Remove Selected/Test used to duplicate the right-click menu
        # below as always-visible buttons -- same convention as every other
        # list page now: those actions live only in the context menu, this
        # is just a status hint.
        self.selection_hint = QLabel("")
        self.selection_hint.setStyleSheet(f"color: {TEXT_DIM};")
        action_row_layout.addWidget(self.selection_hint)
        self.body_layout.addWidget(action_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Launch type", "Executable / URI", "Arguments", "Status", "Added"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 160)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 260)
        self.tree.setColumnWidth(3, 110)
        self.tree.setColumnWidth(4, 140)
        self.tree.itemDoubleClicked.connect(lambda *_: self._edit_windows_app())
        self.tree.itemSelectionChanged.connect(self._update_selection_hint)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.header().sectionClicked.connect(self._on_header_clicked)
        self.body_layout.addWidget(self.tree, 1)

        self._steam_signals = None
        self.reload_from_config()
        self._update_selection_hint()

    def _update_selection_hint(self) -> None:
        count = len(self.tree.selectedItems())
        if count:
            self.selection_hint.setText(f"{count} selected, right-click for actions.")
        else:
            self.selection_hint.setText("Select row(s), then right-click for actions.")

    # == Data ==

    def _windows_dir(self):
        svc.migrate_legacy_windows_stubs(self.window.config_data.get("roms_dir", ""))
        return svc.WINDOWS_STUBS_DIR

    def reload_from_config(self) -> None:
        self._refresh_tree()
        self._refresh_steam_summary()

    def on_shown(self) -> None:
        self._refresh_tree()
        self._refresh_steam_summary()

    def _refresh_tree(self, *_args) -> None:
        selected = {item.text(0) for item in self.tree.selectedItems()}
        self.tree.clear()

        query = self.search_edit.text().strip().casefold()
        apps = svc.load_windows_apps()
        installed_steam_ids = svc.installed_steam_ids()
        rows = list(apps.items())

        def sort_key(pair):
            name, entry = pair
            entry = entry if isinstance(entry, dict) else {}
            display_type = svc.windows_app_display_type(entry) if entry else "Invalid"
            status = svc.windows_app_status(name, entry, installed_steam_ids)
            return svc.windows_app_sort_key(self._sort_column, name, entry, display_type, status)

        rows.sort(key=sort_key, reverse=self._sort_reverse)

        shown = 0
        for name, entry in rows:
            if not isinstance(entry, dict):
                target, args_display, display_type, added = "", "", "Invalid", ""
                status = svc.windows_app_status(name, entry, installed_steam_ids)
            else:
                launch_type = str(entry.get("type", "executable")).lower()
                target = entry.get("uri", "") if launch_type == "uri" else entry.get("exe", "")
                args = entry.get("args", [])
                args_display = " ".join(str(a) for a in args) if isinstance(args, list) and launch_type == "executable" else ""
                display_type = svc.windows_app_display_type(entry)
                added = str(entry.get("added_at", ""))[:10]
                status = svc.windows_app_status(name, entry, installed_steam_ids)

            haystack = f"{name} {display_type} {target} {args_display} {added}".casefold()
            if query and query not in haystack:
                continue

            item = QTreeWidgetItem([name, display_type, str(target), args_display, status, added])
            self.tree.addTopLevelItem(item)
            if name in selected:
                item.setSelected(True)
            shown += 1

        total = len(apps)
        self.count_label.setText(f"{shown} shown / {total} total" if query else f"{total} application(s)")

    def _on_header_clicked(self, index: int) -> None:
        columns = ["name", "type", "target", "args", "status", "added"]
        column = columns[index] if index < len(columns) else "name"
        if column == self._sort_column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self._refresh_tree()

    def _selected_names(self) -> list[str]:
        return [item.text(0) for item in self.tree.selectedItems()]

    # == Steam summary ==

    def _refresh_steam_summary(self) -> None:
        self.steam_summary_label.setText("Steam: scanning libraries...")
        self._steam_signals = run_in_background(self._scan_steam_summary, self._apply_steam_summary)

    def _scan_steam_summary(self):
        games = svc.installed_steam_games()
        installed_ids = {g["appid"] for g in games}
        added_ids = svc.steam_ids_already_added(svc.load_windows_apps())
        in_iisu = len(installed_ids & added_ids)
        available = len(installed_ids - added_ids)
        libraries = len(svc.steam_library_paths())
        return len(games), in_iisu, available, libraries

    def _apply_steam_summary(self, result) -> None:
        total, in_iisu, available, libraries = result
        self.steam_summary_label.setText(
            f"Steam: {total} installed  •  {in_iisu} in iiSU  •  "
            f"{available} available to import  •  {libraries} librar{'y' if libraries == 1 else 'ies'}"
        )

    # == Add / edit / duplicate / remove ==

    def _add_windows_app(self) -> None:
        dialog = WindowsAppDialog(self, "Add Windows Application")
        dialog.exec()
        if not dialog.result_values:
            return
        name, entry = dialog.result_values
        if not name:
            return
        apps = svc.load_windows_apps()
        try:
            svc.create_windows_app(name, entry, apps, self._windows_dir())
            svc.save_windows_apps(apps)
        except svc.WindowsAppsServiceError as e:
            QMessageBox.critical(self, "Windows Apps", str(e))
            return
        self._refresh_tree()

    def _edit_windows_app(self) -> None:
        selected = self._selected_names()
        if not selected:
            QMessageBox.information(self, "Nothing selected", "Select a Windows app first.")
            return
        old_name = selected[0]
        apps = svc.load_windows_apps()
        old_entry = apps.get(old_name)
        if not isinstance(old_entry, dict):
            return
        dialog = WindowsAppDialog(self, "Edit Windows Application", old_name, old_entry)
        dialog.exec()
        if not dialog.result_values:
            return
        new_name, new_entry = dialog.result_values
        if new_name.casefold() != old_name.casefold() and any(k.casefold() == new_name.casefold() for k in apps):
            QMessageBox.critical(self, "Duplicate", f"A Windows app named '{new_name}' already exists.")
            return
        try:
            svc.rename_windows_app_placeholder(self._windows_dir(), old_name, new_name)
        except svc.WindowsAppsServiceError as e:
            QMessageBox.critical(self, "Windows Apps", str(e))
            return
        apps.pop(old_name, None)
        apps[new_name] = new_entry
        svc.save_windows_apps(apps)
        self._refresh_tree()

    def _duplicate_windows_app(self) -> None:
        selected = self._selected_names()
        if len(selected) != 1:
            QMessageBox.information(self, "Select one", "Select one Windows app to duplicate.")
            return
        source_name = selected[0]
        apps = svc.load_windows_apps()
        entry = apps.get(source_name)
        if not isinstance(entry, dict):
            return
        base = f"{source_name} Copy"
        suggested = svc.unique_windows_app_name(base, apps)
        cloned = json.loads(json.dumps(entry))
        dialog = WindowsAppDialog(self, "Duplicate Windows Application", suggested, cloned)
        dialog.exec()
        if not dialog.result_values:
            return
        name, new_entry = dialog.result_values
        try:
            svc.create_windows_app(name, new_entry, apps, self._windows_dir())
            svc.save_windows_apps(apps)
        except svc.WindowsAppsServiceError as e:
            QMessageBox.critical(self, "Windows Apps", str(e))
            return
        self._refresh_tree()

    def _remove_windows_app(self) -> None:
        selected = self._selected_names()
        if not selected:
            QMessageBox.information(self, "Nothing selected", "Select one or more applications first.")
            return
        reply = QMessageBox.question(
            self,
            "Remove Windows Apps?",
            f"Remove {len(selected)} selected application(s) from iiSU-PC?\n\n"
            "Their .pcgame placeholders will also be removed. This does not uninstall the applications themselves.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        apps = svc.load_windows_apps()
        windows_dir = self._windows_dir()
        for name in selected:
            apps.pop(name, None)
            svc.remove_windows_app_placeholder(windows_dir, name)
        svc.save_windows_apps(apps)
        self._refresh_tree()
        self._refresh_steam_summary()

    # == Test / open location ==

    def _test_windows_app(self) -> None:
        selected = self._selected_names()
        if len(selected) != 1:
            QMessageBox.information(self, "Select one", "Select one Windows app to test.")
            return
        name = selected[0]
        entry = svc.load_windows_apps().get(name)
        if not isinstance(entry, dict):
            QMessageBox.critical(self, "Can't test", f"'{name}' has an invalid configuration entry.")
            return

        launch_type = str(entry.get("type", "executable")).lower()
        try:
            if launch_type == "uri":
                uri = svc.resolve_uri_launch(entry)
                os.startfile(uri)
            elif launch_type == "executable":
                executable, args, working_dir = svc.resolve_executable_launch(entry)
                subprocess.Popen([str(executable), *args], cwd=str(working_dir))
            else:
                raise svc.WindowsAppsServiceError(f"'{name}' has an unknown launch type: {launch_type}")
        except (svc.WindowsAppsServiceError, OSError) as e:
            QMessageBox.critical(self, "Launch failed", f"Couldn't launch '{name}'.\n\n{e}")

    def _windows_app_open_or_copy(self) -> None:
        selected = self._selected_names()
        if len(selected) != 1:
            QMessageBox.information(self, "Select one", "Select one Windows app first.")
            return
        name = selected[0]
        entry = svc.load_windows_apps().get(name)
        if not isinstance(entry, dict):
            return
        if str(entry.get("type", "executable")).lower() == "uri":
            uri = str(entry.get("uri", "")).strip()
            if not uri:
                QMessageBox.critical(self, "No URI", f"'{name}' has no URI configured.")
                return
            QApplication.clipboard().setText(uri)
            QMessageBox.information(self, "URI copied", f"Copied to clipboard:\n\n{uri}")
            return

        exe = Path(os.path.expandvars(os.path.expanduser(str(entry.get("exe", "")))))
        if not exe.is_file():
            QMessageBox.critical(self, "Executable not found", f"The configured executable does not exist:\n\n{exe}")
            return
        try:
            subprocess.Popen(["explorer.exe", "/select,", str(exe)])
        except OSError as e:
            QMessageBox.critical(self, "Couldn't open location", str(e))

    # == Repair ==

    def _repair_selected(self) -> None:
        selected = self._selected_names()
        if not selected:
            QMessageBox.information(self, "Nothing selected", "Select one or more applications first.")
            return
        repaired = svc.repair_missing_placeholders(self._windows_dir(), selected)
        self._refresh_tree()
        QMessageBox.information(self, "Repair complete", f"Recreated {repaired} missing placeholder(s).")

    def _sync_repair(self) -> None:
        windows_dir = self._windows_dir()
        apps = svc.load_windows_apps()
        missing, orphans = svc.find_missing_and_orphan_placeholders(apps, windows_dir)
        repaired = svc.repair_missing_placeholders(windows_dir, missing)
        self._refresh_tree()

        summary = []
        if repaired:
            summary.append(f"Recreated {repaired} missing placeholder(s).")
        if orphans:
            summary.append(f"Found {len(orphans)} unconfigured placeholder(s): " + ", ".join(p.name for p in orphans))
        if not summary:
            QMessageBox.information(self, "Windows Apps", "Everything is already in sync. No repairs were needed.")
            return

        if orphans:
            reply = QMessageBox.question(self, "Windows Apps: Sync / Repair", "\n\n".join(summary) + "\n\nConfigure the unconfigured placeholders now?")
            if reply == QMessageBox.StandardButton.Yes:
                apps = svc.load_windows_apps()
                for orphan in orphans:
                    dialog = WindowsAppDialog(self, "Configure Existing Placeholder", orphan.stem)
                    dialog.exec()
                    if not dialog.result_values:
                        continue
                    new_name, entry = dialog.result_values
                    if any(existing.casefold() == new_name.casefold() for existing in apps):
                        QMessageBox.critical(self, "Duplicate", f"A Windows app named '{new_name}' already exists.")
                        continue
                    try:
                        if orphan != windows_dir / f"{new_name}.pcgame":
                            orphan.rename(windows_dir / f"{new_name}.pcgame")
                    except OSError as e:
                        QMessageBox.critical(self, "Windows Apps", f"Couldn't rename the placeholder:\n\n{e}")
                        continue
                    apps[new_name] = entry
                svc.save_windows_apps(apps)
                self._refresh_tree()
                return
        QMessageBox.information(self, "Windows Apps: Sync / Repair", "\n\n".join(summary))

    # == Health check ==

    def _open_health_check(self) -> None:
        dialog = WindowsAppsHealthDialog(self, self._windows_dir(), self._refresh_tree)
        dialog.exec()
        self._refresh_tree()
        self._refresh_steam_summary()

    # == Export / import ==

    def _export_windows_apps(self) -> None:
        apps = svc.load_windows_apps()
        if not apps:
            QMessageBox.information(self, "Export", "There are no Windows Apps to export.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export Windows Apps", "windows_apps_export.json", "JSON files (*.json);;All files (*.*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(svc.build_export_payload(apps), indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        QMessageBox.information(
            self,
            "Export complete",
            "Windows Apps exported.\n\nSteam and URI entries are portable. Executable entries may need their paths updated on another PC.",
        )

    def _import_windows_apps_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Import Windows Apps", filter="JSON files (*.json);;All files (*.*)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return
        incoming = svc.parse_import_payload(payload)
        if incoming is None:
            QMessageBox.critical(self, "Import failed", "That file doesn't contain a Windows Apps mapping.")
            return

        apps = svc.load_windows_apps()
        existing_steam_ids = svc.steam_ids_already_added(apps)
        added, skipped = svc.import_windows_apps(incoming, apps, self._windows_dir(), existing_steam_ids)
        svc.save_windows_apps(apps)
        self._refresh_tree()
        QMessageBox.information(self, "Import complete", f"Imported {added} application(s).\nSkipped {skipped} duplicate/invalid item(s).")

    # == Steam library ==

    def _import_steam_library(self) -> None:
        dialog = SteamLibraryDialog(self, multi_select=True)
        dialog.exec()
        if not dialog.chosen:
            return
        apps = svc.load_windows_apps()
        windows_dir = self._windows_dir()
        existing_steam_ids = svc.steam_ids_already_added(apps)
        imported = skipped = 0
        for game_name, appid in dialog.chosen:
            if appid in existing_steam_ids:
                skipped += 1
                continue
            name = svc.unique_windows_app_name(svc.safe_steam_pcgame_name(game_name), apps)
            entry = {"type": "uri", "uri": f"steam://rungameid/{appid}", "steam_name": game_name}
            try:
                svc.create_windows_app(name, entry, apps, windows_dir)
            except svc.WindowsAppsServiceError:
                skipped += 1
                continue
            imported += 1
            existing_steam_ids.add(appid)
        svc.save_windows_apps(apps)
        self._refresh_tree()
        self._refresh_steam_summary()
        QMessageBox.information(self, "Steam import complete", f"Imported {imported} game(s).\nSkipped {skipped} item(s).")

    def _auto_import_new_steam_games(self) -> None:
        games = svc.installed_steam_games()
        if not games:
            QMessageBox.information(self, "Auto-import Steam Games", "No installed Steam games were found.")
            return
        apps = svc.load_windows_apps()
        already = svc.steam_ids_already_added(apps)
        missing = [g for g in games if g["appid"] not in already]
        if not missing:
            QMessageBox.information(self, "Auto-import Steam Games", "Every installed Steam game is already in iiSU.")
            self._refresh_steam_summary()
            return

        preview = "\n".join(f"• {g['name']}" for g in missing[:12])
        if len(missing) > 12:
            preview += f"\n• ...and {len(missing) - 12} more"
        reply = QMessageBox.question(
            self,
            "Auto-import Steam Games",
            f"Add {len(missing)} installed Steam game(s) that are not currently in iiSU?\n\n{preview}\n\n"
            "This creates the Windows Apps mappings and .pcgame placeholders.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        windows_dir = self._windows_dir()
        imported = skipped = 0
        for game in missing:
            appid = game["appid"]
            name = svc.unique_windows_app_name(svc.safe_steam_pcgame_name(game["name"]), apps)
            entry = {"type": "uri", "uri": f"steam://rungameid/{appid}", "steam_name": game["name"]}
            try:
                svc.create_windows_app(name, entry, apps, windows_dir)
            except svc.WindowsAppsServiceError:
                skipped += 1
                continue
            already.add(appid)
            imported += 1
        svc.save_windows_apps(apps)
        self._refresh_tree()
        self._refresh_steam_summary()
        QMessageBox.information(
            self, "Steam auto-import complete", f"Imported {imported} new Steam game(s)." + (f"\nSkipped {skipped} item(s)." if skipped else "")
        )

    # == Menus ==

    def _show_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is not None and not item.isSelected():
            self.tree.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Launch / Test", self._test_windows_app)
        menu.addAction("Edit...", self._edit_windows_app)
        menu.addAction("Duplicate...", self._duplicate_windows_app)
        menu.addSeparator()
        menu.addAction("Open Location / Copy URI", self._windows_app_open_or_copy)
        menu.addAction("Repair Placeholders", self._repair_selected)
        menu.addSeparator()
        menu.addAction("Remove Selected", self._remove_windows_app)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _show_more_menu(self, anchor_button: QPushButton) -> None:
        menu = QMenu(self)
        menu.addAction("Import Steam Library...", self._import_steam_library)
        menu.addAction("Duplicate...", self._duplicate_windows_app)
        menu.addSeparator()
        menu.addAction("Open Location / Copy URI", self._windows_app_open_or_copy)
        menu.addAction("Sync / Repair...", self._sync_repair)
        menu.addSeparator()
        menu.addAction("Export...", self._export_windows_apps)
        menu.addAction("Import...", self._import_windows_apps_file)
        menu.addSeparator()
        menu.addAction("Open Placeholder Folder", self._open_windows_roms)
        menu.addAction("Delete Old Location...", self._delete_legacy_stubs)
        menu.exec(anchor_button.mapToGlobal(anchor_button.rect().bottomLeft()))

    def _open_windows_roms(self) -> None:
        windows_dir = self._windows_dir()
        try:
            windows_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(windows_dir)
        except OSError as e:
            QMessageBox.critical(self, "Windows Apps", str(e))

    def _delete_legacy_stubs(self) -> None:
        roms_dir = self.window.config_data.get("roms_dir", "")
        legacy_dir = svc.legacy_windows_stubs_dir(roms_dir)
        if legacy_dir is None:
            QMessageBox.information(self, "Windows Apps", "No old placeholder folder found under your ROM directory, nothing to delete.")
            return
        count = sum(1 for _ in legacy_dir.glob("*.pcgame"))
        reply = QMessageBox.question(
            self,
            "Delete Old Placeholder Folder",
            f"Delete {legacy_dir}?\n\nIt holds {count} old .pcgame placeholder(s) left over from before Windows Apps "
            f"placeholders moved to {svc.WINDOWS_STUBS_DIR}. Only do this once you've confirmed your Windows Apps "
            "still work, this cannot be undone.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            svc.delete_legacy_windows_stubs(roms_dir)
        except svc.WindowsAppsServiceError as e:
            QMessageBox.critical(self, "Windows Apps", str(e))
            return
        QMessageBox.information(self, "Windows Apps", f"Deleted {legacy_dir}.")
