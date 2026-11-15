"""PC Emulators settings page.

Leads with the console-first list (bridge/ui/widgets/emulator_overview.py):
which emulator each console opens and whether it's installed, with Download
and Change buttons on each row. The raw mapping table config.json stores
(Android package, executable names, launch flags) sits behind "Show the raw
mapping table" for the rare edit the list can't express.

Both are views of the same mappings: the raw table is the one get_emulators()
reads when saving, and a change made in the list is written into it.
Per the layout rule in pages/base.py, the lists sit directly in the page's
own layout rather than inside a QScrollArea."""

from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from launch_bridge import find_emulator_for_package, find_executable, find_rom
from bridge.ui.dialogs.emulator_dialog import EmulatorDialog
from bridge.ui.dialogs.redirector_install_dialog import RedirectorInstallDialog
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.emulator_overview import EmulatorOverview
from shared.emulator_defaults import build_emulators_map, describe_profile
from shared.qt_theme import TEXT_DIM


class EmulatorsPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window

        self.add_header("PC Emulators", "Which PC emulator each console's games open in.")
        self._shown_once = False

        self.overview = EmulatorOverview(
            get_emulators=self.get_emulators,
            set_emulators=self._fill_tree,
            get_search_roots=lambda: self.window.roms_page.get_search_roots(),
            add_search_root=lambda folder: self.window.roms_page.search_roots_list.addItem(folder),
            get_roms_dir=lambda: self.window.roms_page.get_roms_dir(),
        )
        self.body_layout.addWidget(self.overview, 3)

        self.advanced_toggle = QPushButton("Show the raw mapping table (advanced)")
        self.advanced_toggle.setObjectName("ghost")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self._set_advanced_shown)
        self.body_layout.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.advanced_box = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_box)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        explain = QLabel(
            "iiSU asks Android to open an emulator app by its package name; this table says which PC program "
            "each package name opens instead, with the options it's started with."
        )
        explain.setWordWrap(True)
        explain.setStyleSheet(f"color: {TEXT_DIM};")
        advanced_layout.addWidget(explain)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Android package", "Program name(s)", "Launch options"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 260)
        self.tree.itemSelectionChanged.connect(self._update_selection_hint)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemDoubleClicked.connect(lambda *_: self._edit_emulator())
        advanced_layout.addWidget(self.tree, 1)

        btn_row = QWidget()
        btn_row_layout = QHBoxLayout(btn_row)
        btn_row_layout.setContentsMargins(0, 0, 0, 0)
        add_button = QPushButton("Add...")
        add_button.setObjectName("ghost")
        add_button.clicked.connect(self._add_emulator)
        btn_row_layout.addWidget(add_button)
        self.edit_button = QPushButton("Edit...")
        self.edit_button.setObjectName("ghost")
        self.edit_button.clicked.connect(self._edit_emulator)
        btn_row_layout.addWidget(self.edit_button)
        self.test_button = QPushButton("Test...")
        self.test_button.setObjectName("ghost")
        self.test_button.setToolTip("Says whether the selected mapping's program is found. Save first.")
        self.test_button.clicked.connect(self._test_emulator_mapping)
        btn_row_layout.addWidget(self.test_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("ghost")
        self.remove_button.clicked.connect(self._remove_emulator)
        btn_row_layout.addWidget(self.remove_button)
        btn_row_layout.addStretch(1)
        self.selection_hint = QLabel("")
        self.selection_hint.setStyleSheet(f"color: {TEXT_DIM};")
        btn_row_layout.addWidget(self.selection_hint)
        advanced_layout.addWidget(btn_row)

        btn_row2 = QWidget()
        btn_row2_layout = QHBoxLayout(btn_row2)
        btn_row2_layout.setContentsMargins(0, 0, 0, 0)
        redirector_button = QPushButton("Reinstall iiSU's Emulator Placeholders...")
        redirector_button.setObjectName("ghost")
        redirector_button.setToolTip(
            "iiSU only offers a console once it sees an emulator app for it in Android. Setup installs a "
            "tiny placeholder app for each; this puts them back if they went missing."
        )
        redirector_button.clicked.connect(lambda: RedirectorInstallDialog(self.window).exec())
        btn_row2_layout.addWidget(redirector_button)
        restore_button = QPushButton("Restore Defaults")
        restore_button.setObjectName("ghost")
        restore_button.clicked.connect(self._restore_default_emulators)
        btn_row2_layout.addWidget(restore_button)
        btn_row2_layout.addStretch(1)
        advanced_layout.addWidget(btn_row2)

        self.advanced_box.setVisible(False)
        self.body_layout.addWidget(self.advanced_box, 2)

        self.reload_from_config()
        self._update_selection_hint()

    def _set_advanced_shown(self, shown: bool) -> None:
        self.advanced_box.setVisible(shown)
        self.advanced_toggle.setText(
            "Hide the raw mapping table" if shown else "Show the raw mapping table (advanced)"
        )

    def on_shown(self) -> None:
        """The first visit looks for every emulator (it can take a few
        seconds on a big folder); after that, Check Again does it."""
        if not self._shown_once:
            self._shown_once = True
            self.overview.refresh()

    def reload_from_config(self) -> None:
        self._fill_tree(self.window.config_data.get("emulators", {}))
        if self._shown_once:
            self.overview.refresh()

    def _fill_tree(self, emulators: dict) -> None:
        self.tree.clear()
        for prefix, profile in emulators.items():
            exe_display, pre_args_display = describe_profile(profile)
            self.tree.addTopLevelItem(QTreeWidgetItem([prefix, exe_display, pre_args_display]))
        self._update_selection_hint()

    def get_emulators(self) -> dict:
        original_emulators = self.window.config_data.get("emulators", {})
        emulators = {}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            prefix, exe_names_str, pre_args_str = item.text(0), item.text(1), item.text(2)
            original = original_emulators.get(prefix, {})
            if "by_extension" in original:
                emulators[prefix] = original
                continue
            emulators[prefix] = {
                "exe_names": [s.strip() for s in exe_names_str.split(",") if s.strip()],
                "pre_args": [s.strip() for s in pre_args_str.split(",") if s.strip()],
            }
        return emulators

    def _update_selection_hint(self) -> None:
        count = len(self.tree.selectedItems())
        self.edit_button.setEnabled(count == 1)
        self.test_button.setEnabled(count == 1)
        self.remove_button.setEnabled(count > 0)
        self.selection_hint.setText(f"{count} selected" if count else "")

    def _show_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        if item not in self.tree.selectedItems():
            self.tree.setCurrentItem(item)
        selected = self.tree.selectedItems()

        menu = QMenu(self)
        edit_action = menu.addAction("Edit selected...")
        edit_action.setEnabled(len(selected) == 1)
        test_action = menu.addAction("Test selected...")
        test_action.setEnabled(len(selected) == 1)
        menu.addSeparator()
        remove_action = menu.addAction("Remove selected")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == edit_action:
            self._edit_emulator()
        elif chosen == test_action:
            self._test_emulator_mapping()
        elif chosen == remove_action:
            self._remove_emulator()

    def _find_item(self, prefix: str) -> QTreeWidgetItem | None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.text(0) == prefix:
                return item
        return None

    def _restore_default_emulators(self) -> None:
        reply = QMessageBox.question(
            self,
            "Restore default emulators?",
            "Replaces every mapping with the built-in defaults. Custom or edited mappings "
            "are lost. Save afterward to keep the change.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._fill_tree(build_emulators_map())
        self.overview.refresh()

    def _add_emulator(self) -> None:
        dialog = EmulatorDialog(self, "Add emulator mapping")
        dialog.exec()
        if dialog.result_values:
            prefix, exe_names, pre_args = dialog.result_values
            if not prefix:
                return
            if self._find_item(prefix) is not None:
                QMessageBox.critical(self, "Duplicate", f"A mapping for '{prefix}' already exists.")
                return
            self.tree.addTopLevelItem(QTreeWidgetItem([prefix, ", ".join(exe_names), ", ".join(pre_args)]))
            self._raw_table_changed()

    def _edit_emulator(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        prefix = item.text(0)
        if "by_extension" in self.window.config_data.get("emulators", {}).get(prefix, {}):
            QMessageBox.information(
                self,
                "Can't edit here",
                "This entry maps a different executable per ROM file extension, so it "
                "can't be edited as a single executable/flags pair here. Edit config.json instead.",
            )
            return
        dialog = EmulatorDialog(self, "Edit emulator mapping", prefix=item.text(0), exe_names=item.text(1), pre_args=item.text(2))
        dialog.exec()
        if dialog.result_values:
            new_prefix, exe_names, pre_args = dialog.result_values
            index = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(index)
            self.tree.addTopLevelItem(QTreeWidgetItem([new_prefix, ", ".join(exe_names), ", ".join(pre_args)]))
            self._update_selection_hint()
            self._raw_table_changed()

    def _remove_emulator(self) -> None:
        for item in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self._update_selection_hint()
        self._raw_table_changed()

    def _raw_table_changed(self) -> None:
        """Keeps the console list in step with an edit made in the raw table."""
        if self._shown_once:
            self.overview.refresh()

    def _test_emulator_mapping(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            QMessageBox.information(self, "Nothing selected", "Select a mapping in the list first.")
            return
        prefix = selected[0].text(0)
        profile = self.window.config_data.get("emulators", {}).get(prefix)
        if profile is None:
            QMessageBox.critical(self, "Can't test", "This mapping isn't saved yet. Save first, then try again.")
            return

        rom_filename = None
        if "by_extension" in profile:
            rom_path_str, _filter = QFileDialog.getOpenFileName(
                self, "Pick a ROM to test against (this mapping resolves per file extension)"
            )
            if not rom_path_str:
                return
            rom_filename = Path(rom_path_str).name

        resolved = find_emulator_for_package(prefix, {prefix: profile}, rom_filename, None)
        if resolved is None:
            QMessageBox.critical(
                self,
                "No match",
                f"'{prefix}'" + (f" with '{rom_filename}'" if rom_filename else "") + " doesn't match any configured executable.",
            )
            return

        search_roots = [Path(r) for r in self.window.roms_page.get_search_roots()]
        executable = find_executable(resolved["exe_names"], search_roots, {"executables": {}})

        lines = [
            f"Looking for: {', '.join(resolved['exe_names'])}",
            f"Launch flags: {' '.join(resolved['pre_args']) or '(none)'}",
            f"Found: {executable}" if executable else f"Not found in your {len(search_roots)} search folder(s).",
        ]
        if rom_filename:
            roms_dir = Path(self.window.roms_page.get_roms_dir())
            rom_path = find_rom(rom_filename, roms_dir, {"roms": {}})
            lines.append(f"ROM: found at {rom_path}" if rom_path else f"ROM: NOT found under {roms_dir}")

        QMessageBox.information(self, "Test result", "\n".join(lines))
