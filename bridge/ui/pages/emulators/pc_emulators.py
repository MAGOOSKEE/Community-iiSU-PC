"""PC Emulators settings page -- ports manager.py's _build_emulators_page
and its supporting methods (~lines 1071-1193). The package-prefix mapping
list becomes a QTreeWidget (Tk's ttk.Treeview equivalent); per the layout
rule in pages/base.py, it's placed directly in the page's own layout with
an Expanding size policy rather than inside a QScrollArea."""

from pathlib import Path

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from launch_bridge import find_emulator_for_package, find_executable, find_rom
from bridge.ui.dialogs.emulator_dialog import EmulatorDialog
from bridge.ui.dialogs.redirector_install_dialog import RedirectorInstallDialog
from bridge.ui.pages.base import PageBase
from shared.emulator_defaults import build_emulators_map, describe_profile


class EmulatorsPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window

        self.add_header("Emulators", "Maps the Android package name iiSU tries to launch to a real PC emulator.")

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Package prefix", "Executable name(s)", "Launch flags"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 260)
        self.body_layout.addWidget(self.tree, 1)

        btn_row = QWidget()
        btn_row_layout = QHBoxLayout(btn_row)
        btn_row_layout.setContentsMargins(0, 0, 0, 0)
        add_button = QPushButton("Add...")
        add_button.setObjectName("ghost")
        add_button.clicked.connect(self._add_emulator)
        btn_row_layout.addWidget(add_button)
        edit_button = QPushButton("Edit selected...")
        edit_button.setObjectName("ghost")
        edit_button.clicked.connect(self._edit_emulator)
        btn_row_layout.addWidget(edit_button)
        remove_button = QPushButton("Remove selected")
        remove_button.setObjectName("ghost")
        remove_button.clicked.connect(self._remove_emulator)
        btn_row_layout.addWidget(remove_button)
        test_button = QPushButton("Test selected...")
        test_button.setObjectName("ghost")
        test_button.clicked.connect(self._test_emulator_mapping)
        btn_row_layout.addWidget(test_button)
        btn_row_layout.addStretch(1)
        self.body_layout.addWidget(btn_row)

        btn_row2 = QWidget()
        btn_row2_layout = QHBoxLayout(btn_row2)
        btn_row2_layout.setContentsMargins(0, 0, 0, 0)
        redirector_button = QPushButton("Install Redirector Apps...")
        redirector_button.setObjectName("ghost")
        redirector_button.clicked.connect(lambda: RedirectorInstallDialog(self.window).exec())
        btn_row2_layout.addWidget(redirector_button)
        restore_button = QPushButton("Restore Defaults")
        restore_button.setObjectName("ghost")
        restore_button.clicked.connect(self._restore_default_emulators)
        btn_row2_layout.addWidget(restore_button)
        btn_row2_layout.addStretch(1)
        self.body_layout.addWidget(btn_row2)

        self.reload_from_config()

    def reload_from_config(self) -> None:
        self.tree.clear()
        for prefix, profile in self.window.config_data.get("emulators", {}).items():
            exe_display, pre_args_display = describe_profile(profile)
            self.tree.addTopLevelItem(QTreeWidgetItem([prefix, exe_display, pre_args_display]))

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
            "This replaces every mapping in this list with Community-iiSU-PC's built-in defaults "
            "(shared/emulator_defaults.py). Any custom or edited mappings you've added "
            "will be lost. Save afterward to keep the change.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.window.config_data["emulators"] = build_emulators_map()
        self.reload_from_config()

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
                "This entry maps a different executable per ROM file extension "
                "(see shared/emulator_defaults.py) -- editing it as one flat "
                "executable/flags pair isn't supported here. Edit config.json "
                "directly if you need to change it.",
            )
            return
        dialog = EmulatorDialog(self, "Edit emulator mapping", prefix=item.text(0), exe_names=item.text(1), pre_args=item.text(2))
        dialog.exec()
        if dialog.result_values:
            new_prefix, exe_names, pre_args = dialog.result_values
            index = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(index)
            self.tree.addTopLevelItem(QTreeWidgetItem([new_prefix, ", ".join(exe_names), ", ".join(pre_args)]))

    def _remove_emulator(self) -> None:
        for item in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))

    def _test_emulator_mapping(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            QMessageBox.information(self, "Nothing selected", "Select a mapping in the list first.")
            return
        prefix = selected[0].text(0)
        profile = self.window.config_data.get("emulators", {}).get(prefix)
        if profile is None:
            QMessageBox.critical(self, "Can't test", "This mapping hasn't been saved yet. Click Save first, then try again.")
            return

        rom_filename = None
        if "by_extension" in profile:
            rom_path_str, _filter = QFileDialog.getOpenFileName(
                self, "Pick a ROM to test this mapping against (it resolves per file extension)"
            )
            if not rom_path_str:
                return
            rom_filename = Path(rom_path_str).name

        resolved = find_emulator_for_package(prefix, {prefix: profile}, rom_filename, None)
        if resolved is None:
            QMessageBox.critical(
                self,
                "No match",
                f"'{prefix}'" + (f" with '{rom_filename}'" if rom_filename else "") + " doesn't resolve to any configured executable.",
            )
            return

        search_roots = [Path(r) for r in self.window.roms_page.get_search_roots()]
        executable = find_executable(resolved["exe_names"], search_roots, {"executables": {}})

        lines = [
            f"Looking for: {', '.join(resolved['exe_names'])}",
            f"Launch flags: {' '.join(resolved['pre_args']) or '(none)'}",
            f"Found: {executable}" if executable else f"NOT found under any of your {len(search_roots)} search folder(s).",
        ]
        if rom_filename:
            roms_dir = Path(self.window.roms_page.get_roms_dir())
            rom_path = find_rom(rom_filename, roms_dir, {"roms": {}})
            lines.append(f"ROM: found at {rom_path}" if rom_path else f"ROM: NOT found under {roms_dir}")

        QMessageBox.information(self, "Test result", "\n".join(lines))
