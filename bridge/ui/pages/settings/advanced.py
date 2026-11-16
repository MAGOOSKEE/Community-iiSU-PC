"""Advanced settings page: the expert settings that are rarely changed (how
iiSU's window is recognized, the launch bridge's port, the Android VM's name,
debugging). The everyday settings that used to sit here moved to Settings >
General (volume, loading screen, library rescan) and Settings > Controls
(quit keys and controller buttons)."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QLineEdit

from bridge.ui.pages.base import PageBase
from shared.qt_theme import TEXT_DIM
from shared.vm_backend import BACKEND_WAYDROID, resolve_backend


class AdvancedPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header("Advanced", "Rarely needed. Change these only to fix a specific problem.")

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

        self.debug_console_check = QCheckBox("Show the Android VM's and launch bridge's console windows (debugging)")
        self.body_layout.addWidget(self.debug_console_check)
        debug_note = QLabel(
            "Off by default: output goes to emulator.log, bridge.log and stop.log.\n"
            "When on, the loading screen is hidden and those logs aren't written. Applies on the next Start."
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

    def get_debug_show_console_windows(self) -> bool:
        return self.debug_console_check.isChecked()
