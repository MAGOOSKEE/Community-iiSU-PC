"""General settings page: the everyday choices about how starting iiSU
behaves (Android's volume, the loading screen, rescanning the library).
Split out of the Advanced page, which keeps only the expert settings."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox

from bridge.ui.pages.base import PageBase
from shared.qt_theme import TEXT_DIM


THEME_CHOICES = [("Match Windows", "system"), ("Dark", "dark"), ("Light", "light")]


class GeneralPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header("General", "How the Manager looks and how starting iiSU behaves.")

        theme_row = QHBoxLayout()
        theme_label = QLabel("Manager theme:")
        theme_row.addWidget(theme_label)
        self.theme_combo = QComboBox()
        for label, value in THEME_CHOICES:
            self.theme_combo.addItem(label, value)
        self.theme_combo.setAccessibleName("Manager theme")
        theme_label.setBuddy(self.theme_combo)
        theme_row.addWidget(self.theme_combo)
        theme_note = QLabel("Takes effect next time the Manager opens.")
        theme_note.setStyleSheet(f"color: {TEXT_DIM};")
        theme_row.addWidget(theme_note)
        theme_row.addStretch(1)
        self.body_layout.addLayout(theme_row)

        self.auto_rescan_check = QCheckBox("Rescan iiSU's library by itself when games are added or removed")
        self.auto_rescan_check.setToolTip(
            "After a start finds your ROM folder changed, iiSU is asked to run its own \"Rescan full library\", "
            "so new games appear without opening iiSU's settings."
        )
        self.body_layout.addWidget(self.auto_rescan_check)

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

        self.theme_sync_check = QCheckBox("Match Android's light or dark mode to Windows each time iiSU starts")
        self.theme_sync_check.setToolTip(
            "Sets Android's dark mode the same as Windows' app mode (Settings > Personalization > Colors). "
            "iiSU follows it where its own theme is set to follow the system."
        )
        self.body_layout.addWidget(self.theme_sync_check)

        self.show_overlay_check = QCheckBox("Show a loading screen while iiSU starts and while a game opens")
        self.body_layout.addWidget(self.show_overlay_check)
        self.overlay_details_check = QCheckBox("Show details (the start log) on the loading screen")
        self.overlay_details_check.setToolTip(
            "Adds the log of what Community-iiSU-PC is doing under the progress bar while it starts."
        )
        self.body_layout.addWidget(self.overlay_details_check)
        self.show_overlay_check.toggled.connect(self.overlay_details_check.setEnabled)
        self.body_layout.addStretch(1)

        self.reload_from_config()

    def reload_from_config(self) -> None:
        config = self.window.config_data
        self.auto_rescan_check.setChecked(bool(config.get("auto_rescan_library", True)))
        index = self.theme_combo.findData(str(config.get("manager_theme", "system")))
        self.theme_combo.setCurrentIndex(max(0, index))
        self.theme_sync_check.setChecked(bool(config.get("sync_android_theme", False)))
        self.show_overlay_check.setChecked(bool(config.get("show_boot_overlay", True)))
        self.overlay_details_check.setChecked(bool(config.get("boot_overlay_details", False)))
        self.overlay_details_check.setEnabled(self.show_overlay_check.isChecked())
        volume = config.get("startup_volume", 100)
        self.volume_check.setChecked(volume is not None)
        self.volume_spin.setEnabled(volume is not None)
        self.volume_spin.setValue(volume if isinstance(volume, int) and not isinstance(volume, bool) else 100)

    def get_auto_rescan_library(self) -> bool:
        return self.auto_rescan_check.isChecked()

    def get_manager_theme(self) -> str:
        return self.theme_combo.currentData()

    def get_sync_android_theme(self) -> bool:
        return self.theme_sync_check.isChecked()

    def get_startup_volume(self) -> int | None:
        """The percent to set, or None to leave Android's volume alone."""
        return self.volume_spin.value() if self.volume_check.isChecked() else None

    def get_boot_overlay_details(self) -> bool:
        return self.overlay_details_check.isChecked()

    def get_show_boot_overlay(self) -> bool:
        return self.show_overlay_check.isChecked()
