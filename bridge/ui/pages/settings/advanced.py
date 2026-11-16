"""Advanced settings page. Reuses HotkeyEditor/KeyCaptureDialog from onboarding_wizard.py rather than
re-implementing hotkey capture a second time (its own docstring already
anticipated this reuse)."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bridge.ui.onboarding_wizard import HotkeyEditor
from bridge.ui.pages.base import PageBase
from controller_bridge import BUTTON_DISPLAY_NAMES, BUTTON_NAME_TO_BIT, DEFAULT_QUIT_CHORD
from shared.qt_theme import TEXT_DIM
from shared.vm_backend import BACKEND_WAYDROID, resolve_backend


class AdvancedPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header("Advanced", "Names, port and hotkeys. Rarely needed.")

        names = QGridLayout()
        title_label = QLabel("iiSU window title:")
        title_label.setToolTip("How the bridge recognizes iiSU's window. Only change it if iiSU's window has another title.")
        names.addWidget(title_label, 0, 0)
        self.window_title_edit = QLineEdit()
        self.window_title_edit.setFixedWidth(320)
        names.addWidget(self.window_title_edit, 0, 1)

        port_label = QLabel("Launch bridge port:")
        port_label.setToolTip("The local network port iiSU uses to ask the PC to open a game. Change it only if another program uses 7737.")
        names.addWidget(port_label, 1, 0)
        self.port_edit = QLineEdit()
        self.port_edit.setFixedWidth(80)
        names.addWidget(self.port_edit, 1, 1, Qt.AlignmentFlag.AlignLeft)

        self.avd_name_label = QLabel("Android VM name (AVD):")
        names.addWidget(self.avd_name_label, 2, 0)
        self.avd_name_edit = QLineEdit()
        self.avd_name_edit.setFixedWidth(200)
        names.addWidget(self.avd_name_edit, 2, 1, Qt.AlignmentFlag.AlignLeft)
        names.setColumnStretch(2, 1)
        self.body_layout.addLayout(names)

        self._quit_hotkey_editor: HotkeyEditor | None = None
        self._shutdown_hotkey_editor: HotkeyEditor | None = None
        self._hotkey_container = QWidget()
        self._hotkey_layout = QVBoxLayout(self._hotkey_container)
        self._hotkey_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.addWidget(self._hotkey_container)

        self.body_layout.addWidget(QLabel("Controller quit chord (hold all selected buttons at once):"))
        chord_row = QWidget()
        chord_grid = QGridLayout(chord_row)
        chord_grid.setContentsMargins(0, 0, 0, 0)
        self._chord_checks: dict[str, QCheckBox] = {}
        for index, name in enumerate(BUTTON_NAME_TO_BIT):
            check = QCheckBox(BUTTON_DISPLAY_NAMES.get(name, name))
            self._chord_checks[name] = check
            chord_grid.addWidget(check, index // 7, index % 7)
        self.body_layout.addWidget(chord_row)
        chord_note = QLabel("Same action as the quit key. Leave all unchecked to disable.")
        chord_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(chord_note)

        restart_note = QLabel(
            "Port and hotkey changes need a bridge restart.\n"
            "Everything else applies on the next game launch."
        )
        restart_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(restart_note)

        self.volume_check = QCheckBox("Set Android's volume each time iiSU starts")
        self.volume_check.setToolTip("Unticked, the volume stays wherever it was last set inside the Android VM.")
        self.body_layout.addWidget(self.volume_check)
        volume_row = QHBoxLayout()
        volume_label = QLabel("Volume:")
        volume_row.addWidget(volume_label)
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(0, 100)
        self.volume_spin.setSingleStep(5)
        self.volume_spin.setSuffix(" %")
        self.volume_spin.setFixedWidth(100)
        self.volume_spin.setAccessibleName("Android volume when iiSU starts, in percent")
        volume_label.setBuddy(self.volume_spin)
        volume_row.addWidget(self.volume_spin)
        volume_row.addStretch(1)
        self.body_layout.addLayout(volume_row)
        self.volume_check.toggled.connect(self.volume_spin.setEnabled)
        volume_note = QLabel(
            "A new Android VM starts at about half volume, so iiSU's music and sounds are quiet. "
            "This sets the level for you. Applies the next time iiSU starts."
        )
        volume_note.setWordWrap(True)
        volume_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(volume_note)

        self.auto_rescan_check = QCheckBox("Rescan iiSU's library by itself when games are added or removed")
        self.auto_rescan_check.setToolTip(
            "After a start finds your ROM folder changed, iiSU is asked to run its own \"Rescan full library\", "
            "so new games appear without opening iiSU's settings."
        )
        self.body_layout.addWidget(self.auto_rescan_check)

        self.show_overlay_check = QCheckBox("Show the loading overlay during boot and game launch")
        self.body_layout.addWidget(self.show_overlay_check)

        self.overlay_details_check = QCheckBox("Show details (the start log) on the loading screen")
        self.overlay_details_check.setToolTip(
            "Adds the log of what Community-iiSU-PC is doing under the progress bar while it starts."
        )
        self.body_layout.addWidget(self.overlay_details_check)
        self.show_overlay_check.toggled.connect(self.overlay_details_check.setEnabled)

        self.debug_console_check = QCheckBox("Show the Android VM's and launch bridge's console windows (debugging)")
        self.body_layout.addWidget(self.debug_console_check)
        debug_note = QLabel(
            "Off by default: output goes to emulator.log, bridge.log and stop.log.\n"
            "When on, the overlay is hidden and those logs aren't written. Applies on the next Start."
        )
        debug_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(debug_note)
        # Add to Steam / Remove from Steam live in the menu next to Start
        # on the Home page (bridge/ui/pages/home.py).
        self.body_layout.addStretch(1)

        self.reload_from_config()

    def reload_from_config(self) -> None:
        config = self.window.config_data
        self.window_title_edit.setText(config.get("iisu_window_title", ""))
        self.port_edit.setText(str(config.get("bridge_port", 7737)))
        self.avd_name_edit.setText(config.get("avd_name", "iisuwin"))
        # Waydroid has no AVD to name.
        is_waydroid = resolve_backend(config) == BACKEND_WAYDROID
        self.avd_name_label.setVisible(not is_waydroid)
        self.avd_name_edit.setVisible(not is_waydroid)

        while self._hotkey_layout.count():
            item = self._hotkey_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._quit_hotkey_editor = HotkeyEditor(
            "Quit key (force-quits the running emulator, back to iiSU):",
            config.get("quit_hotkey", {"modifiers": [], "key": "escape"}),
            default_key="escape",
        )
        self._hotkey_layout.addWidget(self._quit_hotkey_editor)
        self._shutdown_hotkey_editor = HotkeyEditor(
            "Full-shutdown hotkey (optional):",
            config.get("shutdown_hotkey") or {"modifiers": [], "key": ""},
            default_key="",
        )
        self._hotkey_layout.addWidget(self._shutdown_hotkey_editor)

        initial_chord = set(config.get("controller_quit_chord") or DEFAULT_QUIT_CHORD)
        for name, check in self._chord_checks.items():
            check.setChecked(name in initial_chord)

        self.auto_rescan_check.setChecked(bool(config.get("auto_rescan_library", True)))
        self.show_overlay_check.setChecked(bool(config.get("show_boot_overlay", True)))
        self.overlay_details_check.setChecked(bool(config.get("boot_overlay_details", False)))
        self.overlay_details_check.setEnabled(self.show_overlay_check.isChecked())
        volume = config.get("startup_volume", 100)
        self.volume_check.setChecked(volume is not None)
        self.volume_spin.setEnabled(volume is not None)
        self.volume_spin.setValue(volume if isinstance(volume, int) and not isinstance(volume, bool) else 100)
        self.debug_console_check.setChecked(bool(config.get("debug_show_console_windows", False)))

    # == Field getters (used by ManagerWindow._gather_settings) ==

    def get_window_title(self) -> str:
        return self.window_title_edit.text().strip()

    def get_avd_name(self) -> str:
        return self.avd_name_edit.text().strip() or "iisuwin"

    def get_port(self) -> int | None:
        try:
            return int(self.port_edit.text())
        except ValueError:
            return None

    def get_quit_hotkey(self) -> dict:
        return self._quit_hotkey_editor.read_hotkey()

    def get_shutdown_hotkey(self) -> dict | None:
        return self._shutdown_hotkey_editor.read_hotkey() if self._shutdown_hotkey_editor.get_key() else None

    def get_controller_quit_chord(self) -> list[str]:
        return [name for name, check in self._chord_checks.items() if check.isChecked()]

    def get_startup_volume(self) -> int | None:
        """The percent to set, or None to leave Android's volume alone."""
        return self.volume_spin.value() if self.volume_check.isChecked() else None

    def get_boot_overlay_details(self) -> bool:
        return self.overlay_details_check.isChecked()

    def get_auto_rescan_library(self) -> bool:
        return self.auto_rescan_check.isChecked()

    def get_show_boot_overlay(self) -> bool:

        return self.show_overlay_check.isChecked()

    def get_debug_show_console_windows(self) -> bool:
        return self.debug_console_check.isChecked()
