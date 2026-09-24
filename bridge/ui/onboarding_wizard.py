"""
Step-by-step first-run onboarding: ROM directory, emulator search folders,
display, and hotkeys, walked through one screen at a time with live
feedback -- instead of dropping a fresh install straight into the
Manager's settings pages, meant for occasional later editing (still
reachable afterward from its sidebar).

Replaces bridge/onboarding_wizard.py's tkinter version during the Qt
rewrite. Launched automatically by bridge/ui/setup_app.py right after
setup finishes. Shares its emulator-mapping dialog with the Manager
(bridge/ui/dialogs/emulator_dialog.py) and its data helpers (console-folder
recognition, monitor detection, executable search) with the same modules
the Manager uses, but keeps its own simpler step-flow UI -- a wizard is a
different shape of problem than a settings page.

Architecturally simpler than the tkinter version it replaces: each step is
built once as its own QWidget and kept alive in a QStackedWidget, rather
than destroyed and rebuilt from scratch on every visit. That removes the
old _capture_current_step() dance entirely (copying live widget state back
into plain-Python fields before a rebuild could destroy it) -- a step's
own widgets are simply always there to read from directly.
"""

import json
import sys
from pathlib import Path

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

import setup_wizard

# PySide6 is this file's own GUI toolkit -- has to be confirmed installed
# before the imports below, which need it. This file is also launched
# standalone (as its own process, by bridge/ui/setup_app.py), so it can't
# rely on some other entry point having already checked.
setup_wizard.ensure_pyside6()

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import winapi
from bridge_config import CONFIG_PATH, ConfigMissingError, load_config
from console_names import load_console_lookup, resolve_console_shortname
from launch_bridge import find_executable
from shared.emulator_defaults import all_emulator_exe_names, describe_profile

from bridge.ui.dialogs.emulator_dialog import EmulatorDialog
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.display_preview import DisplayPreview
from bridge.ui.widgets.gradient_divider import GradientDivider
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import GREEN, GRADIENT_STOPS, RED, SPACING_LG, SPACING_MD, SPACING_SM, Fonts, apply_theme

RESOLUTION_PRESETS = ["1280 x 720", "1600 x 900", "1920 x 1080", "2560 x 1440", "3840 x 2160"]
REFRESH_RATE_PRESETS = ["60", "90", "120", "144", "165", "240"]
MODIFIER_NAMES = ["ctrl", "alt", "shift", "win"]

STEP_TITLES = ["Welcome", "ROM Directory", "Emulator Folders", "Emulator Mappings", "Display", "Hotkeys", "Finish"]

# Qt.Key -> the exact lowercase name bridge/launch_bridge.py's NAMED_KEY_VK
# expects (originally a Tk keysym, lowercased) -- anything not listed here
# falls back to the pressed character itself, which already covers every
# plain letter/digit key. Must stay in sync with launch_bridge.py's table;
# that module is untouched by this rewrite, so the *stored* format can't
# drift even though the *capture UI* is being replaced.
_NAMED_KEYS = {
    Qt.Key.Key_Escape: "escape", Qt.Key.Key_Tab: "tab", Qt.Key.Key_Return: "return",
    Qt.Key.Key_Enter: "return", Qt.Key.Key_Space: "space",
    Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
    Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "prior", Qt.Key.Key_PageDown: "next",
    **{getattr(Qt.Key, f"Key_F{n}"): f"f{n}" for n in range(1, 13)},
}
_MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def read_avd_display(avd_name: str) -> dict:
    """The ground truth for whether display settings actually need
    (re)applying is the AVD's own config.ini, not config.json's "display"
    block -- that block gets written from a generic template default
    regardless of whether it was ever really applied to the VM's hardware
    profile (see apply_display.py). Returns {} if it can't be read, which
    callers should treat as "assume changed" rather than "assume matches"."""
    import apply_display

    config_ini = apply_display.avd_config_path(avd_name)
    if not config_ini.is_file():
        return {}
    values = {}
    key_map = {
        "hw.lcd.width": "width", "hw.lcd.height": "height",
        "hw.lcd.density": "density", "hw.lcd.vsync": "refresh_rate",
    }
    for line in config_ini.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key in key_map:
            values[key_map[key]] = value.strip()
    return values


def _hotkey_signature(hotkey: dict) -> tuple[frozenset, str]:
    """Case/order-insensitive so a hotkey re-saved in a different modifier
    order (or case) than it was originally stored in doesn't register as
    "changed" when it isn't."""
    return (frozenset(m.lower() for m in hotkey.get("modifiers", [])), str(hotkey.get("key", "")).lower())


class KeyCaptureDialog(QDialog):
    """Modal key capture -- upgrades the old tkinter approach (temporarily
    stealing the main window's own keypress binding while a button read
    "press any key...") to a real dialog: modal focus trapping and Esc-to-
    cancel are idiomatic here in a way they weren't for a plain button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Map a key")
        self.setModal(True)
        self.setFixedSize(300, 110)
        self.captured_key: str | None = None

        layout = QVBoxLayout(self)
        label = QLabel("Press any key...\n(Esc cancels)")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(Fonts.heading())
        layout.addWidget(label)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        if key in _MODIFIER_KEYS:
            return  # keep listening -- a bare modifier press isn't a usable hotkey key
        if key in _NAMED_KEYS:
            self.captured_key = _NAMED_KEYS[key]
        else:
            text = event.text().strip()
            self.captured_key = text.lower() if text else None
        if self.captured_key:
            self.accept()


class HotkeyEditor(QWidget):
    """One "modifiers + key" row (quit hotkey, shutdown hotkey) -- used
    twice by HotkeysStep, and reused as-is by whatever Manager Advanced
    page eventually replaces bridge/manager.py's identical hotkey editor."""

    def __init__(self, title: str, initial: dict, default_key: str, parent=None):
        super().__init__(parent)
        self._default_key = default_key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        initial_mods = {m.lower() for m in initial.get("modifiers", [])}
        mod_row = QHBoxLayout()
        self._mod_checks: dict[str, QCheckBox] = {}
        for name in MODIFIER_NAMES:
            check = QCheckBox(name.capitalize())
            check.setChecked(name in initial_mods)
            mod_row.addWidget(check)
            self._mod_checks[name] = check
        mod_row.addStretch()
        layout.addLayout(mod_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("+"))
        self._key = str(initial.get("key", default_key))
        self.key_label = QLabel(self._key.upper())
        self.key_label.setFixedWidth(60)
        self.key_label.setStyleSheet("background-color: #0e0e10; padding: 4px; border-radius: 4px;")
        key_row.addWidget(self.key_label)
        map_button = QPushButton("Map")
        map_button.clicked.connect(self._capture)
        key_row.addWidget(map_button)
        key_row.addStretch()
        layout.addLayout(key_row)

    def _capture(self) -> None:
        dialog = KeyCaptureDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.captured_key:
            self._key = dialog.captured_key
            self.key_label.setText(self._key.upper())

    def get_modifiers(self) -> list[str]:
        return [name for name, check in self._mod_checks.items() if check.isChecked()]

    def get_key(self) -> str:
        return self._key.strip() or self._default_key

    def read_hotkey(self) -> dict:
        return {"modifiers": self.get_modifiers(), "key": self.get_key()}

    def describe(self) -> str:
        mods = [name.capitalize() for name in self.get_modifiers()]
        key = self.get_key().upper()
        return " + ".join([*mods, key]) if mods else key


class WelcomeStep(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("Let's get Community-iiSU-PC set up.")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)

        subtitle = QLabel("A few quick questions and you'll be ready to play, no manual\nconfig.json editing needed afterward.")
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)
        layout.addSpacing(SPACING_MD)

        for line in [
            "Where your ROMs live", "Where your PC emulators are installed",
            "What resolution the VM should run at", "Your quit / shutdown hotkeys",
        ]:
            row = QHBoxLayout()
            bullet = QLabel("•")
            bullet.setStyleSheet(f"color: {GRADIENT_STOPS[2]};")
            row.addWidget(bullet)
            row.addWidget(QLabel(line))
            row.addStretch()
            layout.addLayout(row)

        layout.addSpacing(SPACING_MD)
        footer = QLabel("Takes under a minute. Anything here can be changed again later from\nthe Manager's Configure button.")
        footer.setProperty("role", "dim")
        layout.addWidget(footer)
        layout.addStretch()


class RomsStep(QWidget):
    def __init__(self, roms_dir: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("Where are your ROMs?")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)

        subtitle = QLabel("Pick the root folder that contains one subfolder per console (e.g. psx/, snes/, gc/).")
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        row = QHBoxLayout()
        self.path_edit = QLineEdit(roms_dir)
        self.path_edit.editingFinished.connect(self.refresh_status)
        row.addWidget(self.path_edit, stretch=1)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse)
        row.addWidget(browse_button)
        layout.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch()

        self.refresh_status()

    def roms_dir(self) -> str:
        return self.path_edit.text().strip()

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select root ROM folder")
        if path:
            self.path_edit.setText(path)
            self.refresh_status()

    def refresh_status(self) -> None:
        raw = self.roms_dir()
        if not raw:
            self.status_label.setText("")
            return
        path = Path(raw)
        if not path.is_dir():
            self.status_label.setText("This folder doesn't exist yet. Create it or pick a different one.")
            self.status_label.setStyleSheet(f"color: {RED};")
            return

        exact, by_compact = load_console_lookup()
        recognized, unrecognized = [], []
        for child in sorted(path.iterdir()):
            if not child.is_dir():
                continue
            (recognized if resolve_console_shortname(child.name, exact, by_compact) else unrecognized).append(child.name)

        if not recognized and not unrecognized:
            self.status_label.setText("This folder is empty, that's fine, add ROMs to it anytime.")
            self.status_label.setStyleSheet("")
            self.status_label.setProperty("role", "dim")
        elif not unrecognized:
            self.status_label.setText(f"✓ iiSU will recognize all {len(recognized)} folder(s): {', '.join(recognized)}")
            self.status_label.setStyleSheet(f"color: {GREEN};")
        else:
            prefix = f"✓ {len(recognized)} recognized, " if recognized else ""
            self.status_label.setText(f"{prefix}✗ {len(unrecognized)} won't be seen by iiSU (rename these): {', '.join(unrecognized)}")
            self.status_label.setStyleSheet(f"color: {RED};")

    def validate(self) -> tuple[bool, str]:
        raw = self.roms_dir()
        if not raw:
            return False, "Pick a ROM folder to continue."
        if not Path(raw).is_dir():
            return False, "That folder doesn't exist yet -- create it or pick a different one."
        return True, ""


class EmulatorFoldersStep(QWidget):
    def __init__(self, search_roots: list[str], parent=None):
        super().__init__(parent)
        self.scan_results: list[tuple[str, bool]] | None = None
        self._running = False

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("Where are your PC emulators installed?")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)

        subtitle = QLabel(
            "Community-iiSU-PC searches these folders for the emulators it knows about "
            "(DuckStation, Dolphin, RetroArch, and more) -- install those yourself first "
            "if you haven't already, this project doesn't bundle them."
        )
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        self.roots_list = QListWidget()
        self.roots_list.addItems(search_roots)
        self.roots_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.roots_list.setFixedHeight(110)
        layout.addWidget(self.roots_list)

        btn_row = QHBoxLayout()
        add_button = QPushButton("Add folder...")
        add_button.clicked.connect(self._add_root)
        btn_row.addWidget(add_button)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self._remove_root)
        btn_row.addWidget(remove_button)
        self.scan_button = QPushButton("Scan for installed emulators")
        self.scan_button.clicked.connect(self._start_scan)
        btn_row.addWidget(self.scan_button)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "dim")
        layout.addWidget(self.status_label)
        layout.addStretch()

    def search_roots(self) -> list[str]:
        return [self.roots_list.item(i).text() for i in range(self.roots_list.count())]

    def _add_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select a folder to search for emulators")
        if path:
            self.roots_list.addItem(path)

    def _remove_root(self) -> None:
        for item in self.roots_list.selectedItems():
            self.roots_list.takeItem(self.roots_list.row(item))

    def is_running(self) -> bool:
        return self._running

    def _start_scan(self) -> None:
        if self._running:
            return
        roots = self.search_roots()
        if not roots:
            self.status_label.setText("Add at least one folder above to scan.")
            self.status_label.setStyleSheet(f"color: {RED};")
            return
        self._running = True
        self.scan_button.setEnabled(False)
        self.status_label.setStyleSheet("")
        self.status_label.setText("Scanning. This can take a few seconds for large folders like Program Files...")
        self._scan_signals = run_in_background(self._run_scan, self._on_scan_finished, roots=roots)

    def _run_scan(self, roots: list[str]) -> list[tuple[str, bool]]:
        search_paths = [Path(r) for r in roots]
        cache: dict = {"executables": {}}
        results = []
        for label, exe_names in all_emulator_exe_names():
            found = find_executable(exe_names, search_paths, cache) is not None
            results.append((label, found))
        return results

    def _on_scan_finished(self, results: list[tuple[str, bool]]) -> None:
        self._running = False
        self.scan_button.setEnabled(True)
        self.scan_results = results
        self._render_results()

    def _render_results(self) -> None:
        if self.scan_results is None:
            return
        found_labels = [label for label, ok in self.scan_results if ok]
        missing_labels = [label for label, ok in self.scan_results if not ok]
        total = len(self.scan_results)
        lines = [f"Found {len(found_labels)} of {total} known emulators: {', '.join(found_labels) or '(none yet)'}"]
        if missing_labels:
            lines.append(f"Not found yet: {', '.join(missing_labels)} -- install any of these and Community-iiSU-PC will pick them up automatically.")
        self.status_label.setText("\n".join(lines))
        self.status_label.setStyleSheet(f"color: {GREEN};" if found_labels else "")


class EmulatorMappingsStep(QWidget):
    def __init__(self, emulators: dict, parent=None):
        super().__init__(parent)
        self._original_emulators = dict(emulators)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("Emulator mappings")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)

        subtitle = QLabel(
            "Maps each console's Android package to the real PC emulator that runs it. "
            "The defaults above already cover most installs -- edit here only if you're "
            "using an unusual fork with a different executable name (e.g. a build of "
            "Azahar that ships as azahar.exe instead of citra-qt.exe)."
        )
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Package prefix", "Executable name(s)", "Launch flags"])
        self.tree.setRootIsDecorated(False)
        for prefix, profile in emulators.items():
            exe_display, pre_args_display = describe_profile(profile)
            self.tree.addTopLevelItem(QTreeWidgetItem([prefix, exe_display, pre_args_display]))
        self.tree.setColumnWidth(0, 230)
        self.tree.setColumnWidth(1, 210)
        layout.addWidget(self.tree, stretch=1)

        btn_row = QHBoxLayout()
        add_button = QPushButton("Add...")
        add_button.clicked.connect(self._add_mapping)
        btn_row.addWidget(add_button)
        edit_button = QPushButton("Edit selected...")
        edit_button.clicked.connect(self._edit_mapping)
        btn_row.addWidget(edit_button)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self._remove_mapping)
        btn_row.addWidget(remove_button)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _find_item(self, prefix: str):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.text(0) == prefix:
                return item
        return None

    def _add_mapping(self) -> None:
        dialog = EmulatorDialog(self, "Add emulator mapping")
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_values:
            prefix, exe_names, pre_args = dialog.result_values
            if not prefix:
                return
            if self._find_item(prefix) is not None:
                QMessageBox.critical(self, "Duplicate", f"A mapping for '{prefix}' already exists.")
                return
            self.tree.addTopLevelItem(QTreeWidgetItem([prefix, ", ".join(exe_names), ", ".join(pre_args)]))

    def _edit_mapping(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        prefix = item.text(0)
        if "by_extension" in self._original_emulators.get(prefix, {}):
            QMessageBox.information(
                self, "Can't edit here",
                "This entry maps a different executable per ROM file extension "
                "(see shared/emulator_defaults.py) -- editing it as one flat "
                "executable/flags pair isn't supported here. Edit config.json "
                "directly if you need to change it.",
            )
            return
        dialog = EmulatorDialog(self, "Edit emulator mapping", prefix=item.text(0), exe_names=item.text(1), pre_args=item.text(2))
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_values:
            new_prefix, exe_names, pre_args = dialog.result_values
            index = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(index)
            self.tree.addTopLevelItem(QTreeWidgetItem([new_prefix, ", ".join(exe_names), ", ".join(pre_args)]))

    def _remove_mapping(self) -> None:
        for item in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))

    def get_emulators(self) -> dict:
        """"by_extension" entries (RetroArch, which maps a different real
        PC emulator per ROM extension rather than one fixed exe) show a
        human-readable summary in the tree (see describe_profile), not the
        real underlying data -- always keep the original entry verbatim
        rather than reconstructing it from that summary text. The edit
        dialog already refuses to open on these, so the only way one of
        these rows changes at all is via Remove."""
        emulators = {}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            prefix, exe_names_str, pre_args_str = item.text(0), item.text(1), item.text(2)
            original_entry = self._original_emulators.get(prefix, {})
            if "by_extension" in original_entry:
                emulators[prefix] = original_entry
                continue
            emulators[prefix] = {
                "exe_names": [s.strip() for s in exe_names_str.split(",") if s.strip()],
                "pre_args": [s.strip() for s in pre_args_str.split(",") if s.strip()],
            }
        return emulators


class DisplayStep(QWidget):
    def __init__(self, display: dict, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("What resolution should the VM run at?")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)

        subtitle = QLabel("iiSU's default is a portrait phone screen, this switches it to a\nreal desktop-shaped display. Pre-filled from your primary monitor.")
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        columns = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Resolution:"))
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTION_PRESETS)
        self.resolution_combo.setCurrentIndex(-1)
        self.resolution_combo.currentTextChanged.connect(self._apply_resolution_preset)
        preset_row.addWidget(self.resolution_combo)
        preset_row.addStretch()
        left.addLayout(preset_row)

        exact_row = QHBoxLayout()
        exact_row.addWidget(QLabel("or exactly:"))
        self.width_edit = QLineEdit(str(display.get("width", 1920)))
        self.width_edit.setFixedWidth(60)
        exact_row.addWidget(self.width_edit)
        exact_row.addWidget(QLabel("x"))
        self.height_edit = QLineEdit(str(display.get("height", 1080)))
        self.height_edit.setFixedWidth(60)
        exact_row.addWidget(self.height_edit)
        exact_row.addStretch()
        left.addLayout(exact_row)

        refresh_row = QHBoxLayout()
        refresh_row.addWidget(QLabel("Refresh rate:"))
        self.refresh_edit = QLineEdit(str(display.get("refresh_rate", 60)))
        self.refresh_edit.setFixedWidth(50)
        refresh_row.addWidget(self.refresh_edit)
        refresh_combo = QComboBox()
        refresh_combo.addItems(REFRESH_RATE_PRESETS)
        refresh_combo.setCurrentIndex(-1)
        refresh_combo.currentTextChanged.connect(lambda text: self.refresh_edit.setText(text) if text else None)
        refresh_row.addWidget(refresh_combo)
        refresh_row.addWidget(QLabel("Hz"))
        refresh_row.addStretch()
        left.addLayout(refresh_row)

        redetect_button = QPushButton("Re-detect from primary monitor")
        redetect_button.clicked.connect(lambda: self._autodetect(silent=False))
        left.addWidget(redetect_button, alignment=Qt.AlignmentFlag.AlignLeft)
        left.addStretch()

        right.addWidget(QLabel("Preview"))
        self.preview = DisplayPreview(box_width=160, box_height=100)
        right.addWidget(self.preview)
        right.addStretch()

        columns.addLayout(left)
        columns.addSpacing(SPACING_LG + 4)
        columns.addLayout(right)
        layout.addLayout(columns)

        self.fullscreen_check = QCheckBox("Maximize the iiSU/AVD window automatically")
        layout.addWidget(self.fullscreen_check)
        note = QLabel(
            "Only affects iiSU's own UI inside the VM. Actual gameplay runs in a\n"
            "separate native emulator window at your monitor's real resolution already."
        )
        note.setProperty("role", "dim")
        layout.addWidget(note)
        layout.addStretch()

        self.width_edit.textChanged.connect(self._redraw_preview)
        self.height_edit.textChanged.connect(self._redraw_preview)
        self._redraw_preview()

    def _apply_resolution_preset(self, text: str) -> None:
        if "x" not in text:
            return
        width, height = (part.strip() for part in text.split("x"))
        self.width_edit.setText(width)
        self.height_edit.setText(height)

    def _autodetect(self, silent: bool) -> None:
        try:
            width, height, hz = winapi.get_primary_monitor_mode()
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "Couldn't detect monitor", str(e))
            return
        self.width_edit.setText(str(width))
        self.height_edit.setText(str(height))
        self.refresh_edit.setText(str(hz))

    def _redraw_preview(self, *_args) -> None:
        try:
            width, height = int(self.width_edit.text()), int(self.height_edit.text())
        except ValueError:
            return
        if width > 0 and height > 0:
            self.preview.set_resolution(width, height)

    def set_fullscreen_default(self, value: bool) -> None:
        self.fullscreen_check.setChecked(value)


class HotkeysStep(QWidget):
    def __init__(self, quit_hotkey: dict, shutdown_hotkey: dict, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("Hotkeys")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)
        subtitle = QLabel("Used inside the VM to get back out of a game (both rebindable later too).")
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        self.quit_editor = HotkeyEditor("Quit to iiSU (force-quits the running game):", quit_hotkey, default_key="q")
        layout.addWidget(self.quit_editor)
        self.shutdown_editor = HotkeyEditor("Full shutdown (closes iiSU and the VM entirely):", shutdown_hotkey, default_key="x")
        layout.addWidget(self.shutdown_editor)

        note = QLabel(
            "Also works with a controller: press Select+Start together on any pad for the same\n"
            "quit action, no keyboard needed. Remappable later in Advanced."
        )
        note.setProperty("role", "dim")
        layout.addWidget(note)
        layout.addStretch()


class FinishStep(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(4)
        self.finish_status_label: QLabel | None = None
        self._built = False

    def refresh(self, summary_lines: list[str], missing_warning: str) -> None:
        # setParent(None), not deleteLater(): deleteLater() only schedules
        # deletion for the next event-loop pass, so a widget it "removed"
        # was still being painted (overlapping the freshly added ones) for
        # however long that took -- visible, confirmed live, whenever
        # refresh() ran more than once before Qt caught up.
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        heading = QLabel("Ready to go")
        heading.setFont(Fonts.heading())
        self._layout.addWidget(heading)
        subtitle = QLabel("Here's what's about to be saved:")
        subtitle.setProperty("role", "dim")
        self._layout.addWidget(subtitle)

        for line in summary_lines:
            label = QLabel(f"•  {line}")
            label.setWordWrap(True)
            self._layout.addWidget(label)

        if missing_warning:
            warning = QLabel(missing_warning)
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {RED};")
            self._layout.addWidget(warning)

        self.finish_status_label = QLabel("")
        self.finish_status_label.setWordWrap(True)
        self.finish_status_label.setProperty("role", "dim")
        self._layout.addWidget(self.finish_status_label)
        self._layout.addStretch()


class OnboardingWizard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Welcome to Community-iiSU-PC")
        self.resize(760, 760)
        self.setMinimumSize(700, 760)

        try:
            self.config_data = load_config()
        except ConfigMissingError as e:
            QMessageBox.critical(self, "Config not found", str(e))
            sys.exit(1)

        self.step_index = 0
        self.scan_results: list[tuple[str, bool]] | None = None

        roms_dir = self.config_data.get("roms_dir", "")
        self._original_roms_dir = "" if "CHANGE-ME" in roms_dir else roms_dir
        self._original_search_roots = [r for r in self.config_data.get("search_roots", []) if "CHANGE-ME" not in r]
        self._original_emulators = dict(self.config_data.get("emulators", {}))
        self.original_avd_display = read_avd_display(self.config_data.get("avd_name", "iisuwin"))
        self._original_quit_hotkey = self.config_data.get("quit_hotkey") or {"modifiers": ["ctrl", "alt"], "key": "q"}
        self._original_shutdown_hotkey = self.config_data.get("shutdown_hotkey") or {"modifiers": ["ctrl", "alt"], "key": "x"}

        display = self.config_data.get("display", {"width": 1920, "height": 1080, "density": 240, "refresh_rate": 60})

        self.welcome_step = WelcomeStep()
        self.roms_step = RomsStep(self._original_roms_dir)
        self.folders_step = EmulatorFoldersStep(self._original_search_roots)
        self.mappings_step = EmulatorMappingsStep(self._original_emulators)
        self.display_step = DisplayStep(display)
        self.display_step.set_fullscreen_default(self.config_data.get("iisu_fullscreen", True))
        self.hotkeys_step = HotkeysStep(self._original_quit_hotkey, self._original_shutdown_hotkey)
        self.finish_step = FinishStep()

        self._steps = [
            self.welcome_step, self.roms_step, self.folders_step, self.mappings_step,
            self.display_step, self.hotkeys_step, self.finish_step,
        ]

        self._build_chrome()
        self.display_step._autodetect(silent=True)
        self._show_step(0)

    # -- Chrome -------------------------------------------------

    def _build_chrome(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(SPACING_LG, 20, SPACING_LG, 20)
        root.setSpacing(6)

        self.title_label = QLabel("")
        self.title_label.setFont(Fonts.title())
        root.addWidget(self.title_label)
        self.step_label = QLabel("")
        self.step_label.setProperty("role", "dim")
        root.addWidget(self.step_label)

        root.addSpacing(4)
        root.addWidget(GradientDivider())
        root.addSpacing(SPACING_SM)

        self.progress = QProgressBar()
        self.progress.setRange(0, len(STEP_TITLES) - 1)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)
        root.addSpacing(SPACING_SM)

        content_card = Card()
        card_layout = QVBoxLayout(content_card)
        card_layout.setContentsMargins(SPACING_MD + 4, SPACING_MD + 4, SPACING_MD + 4, SPACING_MD + 4)
        self.stack = QStackedWidget()
        for step in self._steps:
            self.stack.addWidget(step)
        card_layout.addWidget(self.stack)
        root.addWidget(content_card, stretch=1)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {RED};")
        root.addWidget(self.hint_label)

        nav = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._go_back)
        nav.addWidget(self.back_button)
        nav.addStretch()
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("accent")
        self.next_button.clicked.connect(self._go_next)
        nav.addWidget(self.next_button)
        root.addLayout(nav)

    # -- Step machinery -------------------------------------------------

    def _show_step(self, index: int) -> None:
        self.step_index = index
        self.hint_label.setText("")
        self.progress.setValue(index)
        self.stack.setCurrentIndex(index)

        is_welcome, is_last = index == 0, index == len(STEP_TITLES) - 1
        self.title_label.setText("Welcome to Community-iiSU-PC" if is_welcome else STEP_TITLES[index])
        self.step_label.setText("" if is_welcome or is_last else f"Step {index} of {len(STEP_TITLES) - 2}")

        if is_last:
            self.finish_step.refresh(self._summary_lines(), self._missing_emulators_warning())

        self.back_button.setEnabled(index > 0)
        self.next_button.setText("Finish" if is_last else ("Get Started" if is_welcome else "Next"))
        try:
            self.next_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.next_button.clicked.connect(self._finish if is_last else self._go_next)
        self.next_button.setEnabled(True)

    def _go_next(self) -> None:
        if self.folders_step.is_running():
            return
        if self.step_index == 1:
            ok, message = self.roms_step.validate()
            if not ok:
                self.hint_label.setText(message)
                return
        self._show_step(self.step_index + 1)

    def _go_back(self) -> None:
        if self.folders_step.is_running():
            return
        self._show_step(self.step_index - 1)

    # -- Finish -------------------------------------------------

    def _missing_emulators_warning(self) -> str:
        """Surfaced here, not just on the Emulator Folders step itself, so
        it's the last thing seen before saving rather than something only
        visible if you happen to scroll back -- a console mapped to an
        emulator that was never found here will silently fail to launch
        later with no obvious link back to this step."""
        results = self.folders_step.scan_results
        if results is None:
            return (
                "You haven't scanned for installed PC emulators yet -- go back to "
                "\"Emulator Folders\" and click \"Scan for installed emulators\" to confirm "
                "they'll actually be found before finishing."
            )
        missing = [label for label, ok in results if not ok]
        if not missing:
            return ""
        return (
            f"Still not found: {', '.join(missing)} -- games mapped to these won't launch until "
            "they're installed and you rescan (back on \"Emulator Folders\")."
        )

    def _summary_lines(self) -> list[str]:
        """Only what this pass through the wizard actually changed --
        listing every setting regardless of whether it was touched just
        buries the handful that matter in restating the defaults back."""
        lines = []

        if self.roms_step.roms_dir() != self._original_roms_dir:
            lines.append(f"ROM folder: {self.roms_step.roms_dir() or '(not set)'}")

        search_roots = self.folders_step.search_roots()
        if search_roots != self._original_search_roots:
            results = self.folders_step.scan_results
            if results is not None:
                found = sum(1 for _, ok in results if ok)
                emulator_note = f" ({found}/{len(results)} emulators found)"
            else:
                emulator_note = " (not scanned yet)"
            lines.append(f"Emulator search folders: {len(search_roots)}{emulator_note}")

        if self.mappings_step.get_emulators() != self._original_emulators:
            lines.append(f"Emulator mappings: {len(self.mappings_step.get_emulators())} configured")

        if self._display_changed():
            lines.append(
                f"Display: {self.display_step.width_edit.text()}×{self.display_step.height_edit.text()} "
                f"@ {self.display_step.refresh_edit.text()}Hz (will cold-boot the VM once to apply)"
            )

        if _hotkey_signature(self.hotkeys_step.quit_editor.read_hotkey()) != _hotkey_signature(self._original_quit_hotkey):
            lines.append(f"Quit hotkey: {self.hotkeys_step.quit_editor.describe()}")

        if _hotkey_signature(self.hotkeys_step.shutdown_editor.read_hotkey()) != _hotkey_signature(self._original_shutdown_hotkey):
            lines.append(f"Shutdown hotkey: {self.hotkeys_step.shutdown_editor.describe()}")

        return lines or ["Nothing changed from your existing configuration -- it'll be kept as-is."]

    def _display_changed(self) -> bool:
        current = {
            "width": self.display_step.width_edit.text().strip(),
            "height": self.display_step.height_edit.text().strip(),
            "refresh_rate": self.display_step.refresh_edit.text().strip(),
        }
        if not self.original_avd_display:
            return True
        return any(current.get(k) != self.original_avd_display.get(k) for k in current)

    def _build_final_config(self) -> dict:
        try:
            display = {
                "width": int(self.display_step.width_edit.text()),
                "height": int(self.display_step.height_edit.text()),
                "density": int(self.config_data.get("display", {}).get("density", 240)),
                "refresh_rate": int(self.display_step.refresh_edit.text()),
            }
        except ValueError:
            display = self.config_data.get("display", {})

        config = dict(self.config_data)
        config["roms_dir"] = self.roms_step.roms_dir()
        config["search_roots"] = self.folders_step.search_roots()
        config["emulators"] = self.mappings_step.get_emulators()
        config["display"] = display
        config["iisu_fullscreen"] = self.display_step.fullscreen_check.isChecked()
        config["quit_hotkey"] = self.hotkeys_step.quit_editor.read_hotkey()
        config["shutdown_hotkey"] = self.hotkeys_step.shutdown_editor.read_hotkey()
        return config

    def _finish(self) -> None:
        ok, message = self.roms_step.validate()
        if not ok:
            self._show_step(1)
            self.hint_label.setText(message)
            return

        config = self._build_final_config()
        save_config(config)
        self.config_data = config

        self.back_button.setEnabled(False)
        self.next_button.setEnabled(False)

        # Writes straight into the AVD's own config.ini (no boot needed --
        # see _write_avd_display_profile) rather than cold-booting the VM
        # right here to "apply" it: this AVD always cold-boots on its very
        # first real start regardless, so the setting is already going to
        # be in effect the moment the person starts Community-iiSU-PC
        # themselves.
        if self._display_changed():
            self._write_avd_display_profile(config)

        self.finish_step.finish_status_label.setText(
            "Saved. Start Community-iiSU-PC from the desktop shortcut or Manager's Home page "
            "whenever you're ready. Double-check your ROM folder is reachable from inside the VM, "
            "and rerun the scan on \"Emulator Folders\" if you're not sure your emulators were found."
        )
        self.finish_step.finish_status_label.setStyleSheet(f"color: {GREEN};")
        try:
            self.next_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.next_button.setText("Close")
        self.next_button.clicked.connect(self.close)
        self.next_button.setEnabled(True)

    def _write_avd_display_profile(self, config: dict) -> None:
        """Best-effort: a failure here just leaves the AVD on its previous
        profile until the Manager's Display page is used later, never
        worth blocking onboarding's own completion over."""
        import apply_display

        config_ini = apply_display.avd_config_path(config.get("avd_name", "iisuwin"))
        if not config_ini.is_file():
            return
        try:
            apply_display.update_config_ini(config_ini, config["display"])
        except OSError as e:
            print(f"[onboarding] couldn't write the AVD's display profile ({e}) -- it'll get applied next time Display settings are saved")


def main() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    apply_theme(app)
    window = OnboardingWizard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
