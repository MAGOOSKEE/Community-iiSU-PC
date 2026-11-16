"""
Step-by-step first-run onboarding: ROM directory, emulator search folders,
display, and hotkeys, walked through one screen at a time with live
feedback, instead of dropping a fresh install straight into the
Manager's settings pages, meant for occasional later editing (still
reachable afterward from its sidebar).

Launched automatically by bridge/ui/setup_app.py right after
setup finishes. Shares its emulator-mapping dialog with the Manager
(bridge/ui/dialogs/emulator_dialog.py) and its data helpers (console-folder
recognition, monitor detection, executable search) with the same modules
the Manager uses, but keeps its own simpler step-flow UI, a wizard is a
different shape of problem than a settings page.

Each step is built once as its own QWidget and kept alive in a
QStackedWidget, rather than destroyed and rebuilt on every visit, so a
step's own widgets are always there to read from directly.
"""

import html
import json
import sys
from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

import setup_wizard

# PySide6 is this file's own GUI toolkit, has to be confirmed installed
# before the imports below, which need it. This file is also launched
# standalone (as its own process, by bridge/ui/setup_app.py), so it can't
# rely on some other entry point having already checked.
setup_wizard.ensure_pyside6()

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

import winapi
from bridge_config import CONFIG_PATH, ConfigMissingError, load_config, save_config  # noqa: F401
from console_names import load_console_lookup, resolve_console_shortname, suggest_folder_name

from bridge.ui.dialogs.icon_choice_dialog import IconChoiceDialog
from bridge.ui.widgets.brand_mark import BrandMark
from bridge.ui.widgets.card import Card
from bridge.ui.pages.settings.display import REFRESH_RATE_PRESETS, RESOLUTION_CUSTOM, RESOLUTION_PRESETS, field_width
from bridge.ui.widgets.display_preview import DisplayPreview
from bridge.ui.widgets.emulator_overview import EmulatorOverview
from bridge.ui.widgets.gradient_divider import GradientDivider
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import GREEN, GRADIENT_STOPS, RED, SPACING_LG, SPACING_MD, SPACING_SM, Fonts, apply_theme

MODIFIER_NAMES = ["ctrl", "alt", "shift", "win"]

STEP_TITLES = ["Welcome", "ROM Directory", "Emulator Folders", "Your Emulators", "Display", "Hotkeys", "Finish"]

# Qt.Key -> the exact lowercase name bridge/hotkey_keys.py's NAMED_KEY_VK
# expects, anything not listed here falls back to the pressed character
# itself, which already covers every plain letter/digit key. Must stay in
# sync with hotkey_keys.py's table so the *stored* format can't drift.
_NAMED_KEYS = {
    Qt.Key.Key_Escape: "escape", Qt.Key.Key_Tab: "tab", Qt.Key.Key_Return: "return",
    Qt.Key.Key_Enter: "return", Qt.Key.Key_Space: "space",
    Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
    Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "prior", Qt.Key.Key_PageDown: "next",
    **{getattr(Qt.Key, f"Key_F{n}"): f"f{n}" for n in range(1, 13)},
}
_MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}


def read_avd_display(avd_name: str) -> dict:
    """The ground truth for whether display settings actually need
    (re)applying is the AVD's own config.ini, not config.json's "display"
    block, that block gets written from a generic template default
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
    """Modal key capture: a real dialog gives modal focus trapping.

    Every key press is the key being mapped, Escape included. Escape used to
    cancel the dialog, so the one key that is the default quit key could
    never be mapped again once it had been changed. Cancelling is the Cancel
    button, or closing the window (Alt+F4, or its X)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Map a key")
        self.setModal(True)
        self.setFixedSize(440, 190)
        self.captured_key: str | None = None

        layout = QVBoxLayout(self)
        label = QLabel("Press the key to use")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(Fonts.heading())
        layout.addWidget(label)
        hint = QLabel("Any key works, including Escape.\nTo keep the current key, choose Cancel or press Alt+F4.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setProperty("role", "dim")
        layout.addWidget(hint)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("ghost")
        # Never holds keyboard focus: a focused button would take Space and
        # Enter for itself, and both are keys that can be mapped.
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignHCenter)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in _MODIFIER_KEYS:
            return  # keep listening, a bare modifier press isn't a usable hotkey key
        if key in _NAMED_KEYS:
            self.captured_key = _NAMED_KEYS[key]
        else:
            text = event.text().strip()
            self.captured_key = text.lower() if text else None
        if self.captured_key:
            self.accept()


class HotkeyEditor(QWidget):
    """One "modifiers + key" row (quit hotkey, shutdown hotkey), used
    twice by HotkeysStep, and reused as-is by the Manager's Controls
    page."""

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

        subtitle = QLabel("A few quick questions, then you're ready to play.")
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
        footer = QLabel("Takes under a minute. Everything can be changed later in the Manager.")
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

        subtitle = QLabel("Pick the folder that holds one subfolder per console (e.g. psx/, snes/, gc/).")
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

        self.rename_button = QPushButton("Rename These Folders for Me...")
        self.rename_button.setObjectName("ghost")
        self.rename_button.clicked.connect(self._rename_suggested)
        self.rename_button.setVisible(False)
        layout.addWidget(self.rename_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()

        self._suggestions: dict[str, str] = {}
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
            self.status_label.setText("This folder doesn't exist. Create it or pick another.")
            self.status_label.setStyleSheet(f"color: {RED};")
            return

        exact, by_compact = load_console_lookup()
        recognized, unrecognized = [], []
        for child in sorted(path.iterdir()):
            if not child.is_dir():
                continue
            (recognized if resolve_console_shortname(child.name, exact, by_compact) else unrecognized).append(child.name)

        # Only suggest a rename that wouldn't collide with a folder already there.
        existing = {child.name.lower() for child in path.iterdir() if child.is_dir()}
        self._suggestions = {}
        for name in unrecognized:
            suggestion = suggest_folder_name(name, exact, by_compact)
            if suggestion and suggestion.lower() not in existing:
                self._suggestions[name] = suggestion
        self.rename_button.setVisible(bool(self._suggestions))

        if not recognized and not unrecognized:
            self.status_label.setText("Empty folder. Add ROMs anytime.")
            self.status_label.setStyleSheet("")
            self.status_label.setProperty("role", "dim")
        elif not unrecognized:
            self.status_label.setText(f"iiSU will recognize all {len(recognized)} folder(s): {', '.join(recognized)}")
            self.status_label.setStyleSheet(f"color: {GREEN};")
        else:
            lines = [f"<span style='color:{GREEN}'>{len(recognized)} folder(s) recognized.</span>"] if recognized else []
            lines.append(f"<span style='color:{RED}'>iiSU won't recognize {len(unrecognized)} folder(s):</span>")
            for name in unrecognized:
                safe = html.escape(name)
                if name in self._suggestions:
                    lines.append(f"&nbsp;&nbsp;&nbsp;{safe}: rename it to <b>{html.escape(self._suggestions[name])}</b>")
                else:
                    lines.append(f"&nbsp;&nbsp;&nbsp;{safe}: not a console name iiSU knows, so it's skipped")
            self.status_label.setText("<br>".join(lines))
            self.status_label.setStyleSheet("")

    def _rename_suggested(self) -> None:
        root = Path(self.roms_dir())
        plan = "\n".join(f"{old}  to  {new}" for old, new in self._suggestions.items())
        answer = QMessageBox.question(
            self, "Rename folders?",
            f"Rename these folders in {root}?\n\n{plan}\n\n"
            "Only the folder names change; the games inside stay as they are. Another program "
            "that remembers the old folder names would need pointing at the new ones.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failed = []
        for old, new in self._suggestions.items():
            try:
                (root / old).rename(root / new)
            except OSError as exc:
                failed.append(f"{old}: {exc}")
        if failed:
            QMessageBox.warning(self, "Some folders weren't renamed", "\n".join(failed))
        self.refresh_status()

    def validate(self) -> tuple[bool, str]:
        raw = self.roms_dir()
        if not raw:
            return False, "Pick a ROM folder to continue."
        if not Path(raw).is_dir():
            return False, "That folder doesn't exist. Create it or pick another."
        return True, ""


class EmulatorFoldersStep(QWidget):
    def __init__(self, search_roots: list[str], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("Where do you install PC emulators?")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)

        subtitle = QLabel(
            "Emulators are looked for in these folders and the folders inside them. If you haven't "
            "installed any yet, that's fine: the next step can download them for you."
        )
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        self.roots_list = QListWidget()
        self.roots_list.addItems(search_roots)
        self.roots_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.roots_list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.roots_list, 1)

        btn_row = QHBoxLayout()
        add_button = QPushButton("Add Folder...")
        add_button.clicked.connect(self._add_root)
        btn_row.addWidget(add_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove_root)
        btn_row.addWidget(self.remove_button)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.remove_button.setEnabled(bool(self.roots_list.selectedItems()))

    def search_roots(self) -> list[str]:
        return [self.roots_list.item(i).text() for i in range(self.roots_list.count())]

    def add_root(self, path: str) -> None:
        if path and path not in self.search_roots():
            self.roots_list.addItem(path)

    def _add_root(self) -> None:
        self.add_root(QFileDialog.getExistingDirectory(self, "Select a folder to search for emulators"))

    def _remove_root(self) -> None:
        for item in self.roots_list.selectedItems():
            self.roots_list.takeItem(self.roots_list.row(item))


class EmulatorsStep(QWidget):
    """The same console list as the Manager's PC Emulators page
    (bridge/ui/widgets/emulator_overview.py), with the consoles your ROM
    folder has first and a button that downloads just what they need."""

    def __init__(self, emulators: dict, folders_step: EmulatorFoldersStep, roms_step: "RomsStep", parent=None):
        super().__init__(parent)
        self._emulators = dict(emulators)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("Your emulators")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)
        subtitle = QLabel(
            "Which PC emulator each console's games open in. The consoles in your ROM folder come first. "
            "The defaults suit most people; Change only if you prefer another emulator."
        )
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        self.overview = EmulatorOverview(
            get_emulators=lambda: self._emulators,
            set_emulators=self._set_emulators,
            get_search_roots=folders_step.search_roots,
            add_search_root=folders_step.add_root,
            get_roms_dir=roms_step.roms_dir,
        )
        layout.addWidget(self.overview, 1)

    def _set_emulators(self, emulators: dict) -> None:
        self._emulators = emulators

    def get_emulators(self) -> dict:
        return dict(self._emulators)

    def on_shown(self) -> None:
        self.overview.refresh()


class DisplayStep(QWidget):
    def __init__(self, display: dict, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("What resolution should the VM run at?")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)

        subtitle = QLabel(
            "Pre-filled from your primary monitor. It only affects iiSU's own UI, games run at "
            "your monitor's real resolution."
        )
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        # Laid out like the Manager's Display page (bridge/ui/pages/settings/display.py):
        # one resolution row and one editable refresh rate box, not a preset menu
        # beside every field.
        columns = QHBoxLayout()
        settings_col = QVBoxLayout()
        grid = QGridLayout()

        grid.addWidget(QLabel("Resolution:"), 0, 0)
        resolution_row = QHBoxLayout()
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTION_PRESETS + [RESOLUTION_CUSTOM])
        self.resolution_combo.setCurrentIndex(self.resolution_combo.findText(RESOLUTION_CUSTOM))
        self.resolution_combo.currentTextChanged.connect(self._apply_resolution_preset)
        resolution_row.addWidget(self.resolution_combo)
        self.width_edit = QLineEdit(str(display.get("width", 1920)))
        self.width_edit.setFixedWidth(field_width(self.width_edit, "00000"))
        resolution_row.addWidget(self.width_edit)
        resolution_row.addWidget(QLabel("x"))
        self.height_edit = QLineEdit(str(display.get("height", 1080)))
        self.height_edit.setFixedWidth(field_width(self.height_edit, "00000"))
        resolution_row.addWidget(self.height_edit)
        resolution_row.addStretch(1)
        grid.addLayout(resolution_row, 0, 1)

        grid.addWidget(QLabel("Refresh rate (Hz):"), 1, 0)
        refresh_combo = QComboBox()
        refresh_combo.setEditable(True)
        refresh_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        refresh_combo.addItems(REFRESH_RATE_PRESETS)
        refresh_combo.setFixedWidth(field_width(refresh_combo, "0000") + 28)
        self.refresh_edit = refresh_combo.lineEdit()
        self.refresh_edit.setText(str(display.get("refresh_rate", 60)))
        grid.addWidget(refresh_combo, 1, 1, Qt.AlignmentFlag.AlignLeft)

        settings_col.addLayout(grid)
        redetect_button = QPushButton("Auto-detect from primary monitor")
        redetect_button.setObjectName("ghost")
        redetect_button.clicked.connect(lambda: self._autodetect(silent=False))
        settings_col.addWidget(redetect_button, alignment=Qt.AlignmentFlag.AlignLeft)
        settings_col.addStretch(1)

        preview_col = QVBoxLayout()
        preview_col.addWidget(QLabel("Aspect ratio preview"))
        self.preview = DisplayPreview(box_width=150, box_height=110)
        preview_col.addWidget(self.preview)
        preview_col.addStretch(1)

        columns.addLayout(settings_col, 1)
        columns.addLayout(preview_col)
        layout.addLayout(columns)

        self.fullscreen_check = QCheckBox("Show iiSU fullscreen")
        layout.addWidget(self.fullscreen_check)
        layout.addStretch()

        self.width_edit.textChanged.connect(self._redraw_preview)
        self.height_edit.textChanged.connect(self._redraw_preview)
        self._redraw_preview()

    def _apply_resolution_preset(self, text: str) -> None:
        # "Custom" changes nothing: the width and height boxes are the custom size.
        if text not in RESOLUTION_PRESETS:
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

    def _sync_resolution_preset(self) -> None:
        """Shows the preset that matches the typed width and height, or Custom."""
        text = f"{self.width_edit.text().strip()} x {self.height_edit.text().strip()}"
        index = self.resolution_combo.findText(text if text in RESOLUTION_PRESETS else RESOLUTION_CUSTOM)
        if index != self.resolution_combo.currentIndex():
            self.resolution_combo.blockSignals(True)
            self.resolution_combo.setCurrentIndex(index)
            self.resolution_combo.blockSignals(False)

    def _redraw_preview(self, *_args) -> None:
        self._sync_resolution_preset()
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
        subtitle = QLabel("Used in the VM to get out of a game. Both can be rebound later.")
        subtitle.setProperty("role", "dim")
        layout.addWidget(subtitle)

        self.quit_editor = HotkeyEditor("Quit to iiSU (force-quits the running game):", quit_hotkey, default_key="q")
        layout.addWidget(self.quit_editor)
        self.shutdown_editor = HotkeyEditor("Full shutdown (closes iiSU and the VM):", shutdown_hotkey, default_key="x")
        layout.addWidget(self.shutdown_editor)

        note = QLabel(
            "On a controller, press Select+Start together for the same quit action.\n"
            "Remappable later in Settings > Controls."
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
        # however long that took, visible, confirmed live, whenever
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
        self.resize(920, 760)
        self.setMinimumSize(860, 700)


        try:
            self.config_data = load_config()
        except ConfigMissingError as e:
            QMessageBox.critical(self, "Config not found", str(e))
            sys.exit(1)

        self.step_index = 0

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
        self.mappings_step = EmulatorsStep(self._original_emulators, self.folders_step, self.roms_step)
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

    # == Chrome ==

    def _build_chrome(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(SPACING_LG, 20, SPACING_LG, 20)
        root.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        self.title_label = QLabel("")
        self.title_label.setFont(Fonts.title())
        title_row.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignBottom)
        # "Welcome to" and the iiPC mark on the first step, in place of the
        # product's full name.
        self.welcome_mark = BrandMark(34, animated=True)
        title_row.addWidget(self.welcome_mark, 0, Qt.AlignmentFlag.AlignBottom)
        title_row.addStretch(1)
        root.addLayout(title_row)
        self.step_label = QLabel("")
        self.step_label.setProperty("role", "dim")
        root.addWidget(self.step_label)

        root.addSpacing(4)
        # The line under the title is also the progress through the steps.
        self.progress = GradientDivider(height=4)
        root.addWidget(self.progress)
        root.addSpacing(SPACING_MD)

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

    # == Step machinery ==

    def _show_step(self, index: int) -> None:
        self.step_index = index
        self.hint_label.setText("")
        self.progress.set_fraction(index / (len(STEP_TITLES) - 1))
        self.stack.setCurrentIndex(index)

        is_welcome, is_last = index == 0, index == len(STEP_TITLES) - 1
        show_mark = is_welcome and self.welcome_mark.is_available()
        self.welcome_mark.setVisible(show_mark)
        self.title_label.setText(
            ("Welcome to" if show_mark else "Welcome to Community-iiSU-PC") if is_welcome else STEP_TITLES[index]
        )
        self.step_label.setText("" if is_welcome or is_last else f"Step {index} of {len(STEP_TITLES) - 2}")

        if self._steps[index] is self.mappings_step:
            self.mappings_step.on_shown()
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
        if self.step_index == 1:
            ok, message = self.roms_step.validate()
            if not ok:
                self.hint_label.setText(message)
                return
        self._show_step(self.step_index + 1)

    def _go_back(self) -> None:
        self._show_step(self.step_index - 1)

    # == Finish ==

    def _missing_emulators_warning(self) -> str:
        """The last thing seen before saving: a console in your library whose
        emulator isn't installed fails to launch later with no obvious link
        back to this wizard."""
        missing = self.mappings_step.overview.missing_in_library()
        if not missing:
            return ""
        needs = ", ".join(sorted({f"{row.emulator} ({row.console_label})" for row in missing}))
        return (
            f"Not installed yet: {needs}. Those games won't open until it is. "
            "Go back to \"Your Emulators\" to download it, or do it later in the Manager."
        )

    def _summary_lines(self) -> list[str]:
        """Only what this pass through the wizard actually changed,
        listing every setting regardless of whether it was touched just
        buries the handful that matter in restating the defaults back."""
        lines = []

        if self.roms_step.roms_dir() != self._original_roms_dir:
            lines.append(f"ROM folder: {self.roms_step.roms_dir() or '(not set)'}")

        search_roots = self.folders_step.search_roots()
        if search_roots != self._original_search_roots:
            lines.append(f"Emulator folders: {len(search_roots)}")

        rows = self.mappings_step.overview.rows
        if rows:
            ready = [row for row in rows if row.in_library and row.status == "found"]
            if ready:
                lines.append(f"Ready to play: {', '.join(row.console_label for row in ready)}")
        changed = [row for row in rows if not row.is_default and row.console]
        if self.mappings_step.get_emulators() != self._original_emulators and changed:
            lines.append("Emulators changed: " + ", ".join(f"{row.console_label} opens in {row.emulator}" for row in changed))

        if self._display_changed():
            lines.append(
                f"Display: {self.display_step.width_edit.text()}×{self.display_step.height_edit.text()} "
                f"@ {self.display_step.refresh_edit.text()}Hz (the first start takes a little longer to apply it)"
            )

        if _hotkey_signature(self.hotkeys_step.quit_editor.read_hotkey()) != _hotkey_signature(self._original_quit_hotkey):
            lines.append(f"Quit hotkey: {self.hotkeys_step.quit_editor.describe()}")

        if _hotkey_signature(self.hotkeys_step.shutdown_editor.read_hotkey()) != _hotkey_signature(self._original_shutdown_hotkey):
            lines.append(f"Shutdown hotkey: {self.hotkeys_step.shutdown_editor.describe()}")

        return lines or ["Nothing changed. Your existing configuration is kept."]

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
        # Setup made the shortcuts just before this, with iiSU's own icon.
        icon_dialog = IconChoiceDialog(config.get("icon_choice"), self, offer_no_shortcut=True)
        icon_chosen = icon_dialog.exec() == QDialog.DialogCode.Accepted
        if icon_chosen:
            config["icon_choice"] = icon_dialog.choice()
        save_config(config)
        self.config_data = config
        if icon_chosen:
            self._shortcut_signals = run_in_background(
                self._remake_shortcuts, None, self._on_shortcuts_failed,
                config["icon_choice"], icon_dialog.wants_shortcut(),
            )

        self.back_button.setEnabled(False)
        self.next_button.setEnabled(False)

        # Writes straight into the AVD's own config.ini (no boot needed,
        # see _write_avd_display_profile) rather than cold-booting the VM
        # right here to "apply" it: this AVD always cold-boots on its very
        # first real start regardless, so the setting is already going to
        # be in effect the moment the person starts Community-iiSU-PC
        # themselves.
        if self._display_changed():
            self._write_avd_display_profile(config)

        start_from = (
            "the desktop shortcut or the Manager's Home page"
            if not icon_chosen or icon_dialog.wants_shortcut() else "the Manager's Home page"
        )
        self.finish_step.finish_status_label.setText(
            f"Saved. Start Community-iiSU-PC from {start_from}. "
            "Check your ROM folder is reachable from inside the VM, and rescan \"Emulator Folders\" "
            "if you're unsure your emulators were found."
        )
        self.finish_step.finish_status_label.setStyleSheet(f"color: {GREEN};")
        try:
            self.next_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.next_button.setText("Close")
        self.next_button.clicked.connect(self.close)
        self.next_button.setEnabled(True)

    @staticmethod
    def _remake_shortcuts(icon_choice: str, wants_shortcut: bool):
        import create_shortcut

        if wants_shortcut:
            return create_shortcut.create_desktop_shortcut(icon_choice=icon_choice)
        return create_shortcut.remove_desktop_shortcut(icon_choice)

    def _on_shortcuts_failed(self, message: str) -> None:
        # Not worth stopping onboarding over: Recreate Shortcut on Home tries again.
        print(f"[onboarding] couldn't remake the desktop shortcuts with the chosen icon: {message}")

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
            print(f"[onboarding] couldn't write the AVD's display profile ({e}), it'll get applied next time Display settings are saved")


def main() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    apply_theme(app)
    window = OnboardingWizard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
