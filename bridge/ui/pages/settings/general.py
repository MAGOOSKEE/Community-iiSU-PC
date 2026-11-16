"""General settings page: the everyday choices about how starting iiSU
behaves (Android's volume, the loading screen, rescanning the library).
Split out of the Advanced page, which keeps only the expert settings."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSpinBox

from bridge.ui.pages.base import PageBase
from shared.qt_theme import TEXT_DIM


class GeneralPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header("General", "How starting iiSU behaves.")

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
        self.show_overlay_check.setChecked(bool(config.get("show_boot_overlay", True)))
        self.overlay_details_check.setChecked(bool(config.get("boot_overlay_details", False)))
        self.overlay_details_check.setEnabled(self.show_overlay_check.isChecked())
        volume = config.get("startup_volume", 100)
        self.volume_check.setChecked(volume is not None)
        self.volume_spin.setEnabled(volume is not None)
        self.volume_spin.setValue(volume if isinstance(volume, int) and not isinstance(volume, bool) else 100)

    def get_auto_rescan_library(self) -> bool:
        return self.auto_rescan_check.isChecked()

    def get_startup_volume(self) -> int | None:
        """The percent to set, or None to leave Android's volume alone."""
        return self.volume_spin.value() if self.volume_check.isChecked() else None

    def get_boot_overlay_details(self) -> bool:
        return self.overlay_details_check.isChecked()

    def get_show_boot_overlay(self) -> bool:
        return self.show_overlay_check.isChecked()
