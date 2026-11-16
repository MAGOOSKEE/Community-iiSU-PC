"""Display settings page. Reuses
DisplayPreview (bridge/ui/widgets/display_preview.py), the same widget
onboarding_wizard.py's DisplayStep uses, instead of a second hand-drawn
aspect-ratio canvas."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import winapi
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.display_preview import DisplayPreview
from shared.qt_theme import TEXT_DIM

RESOLUTION_PRESETS = ["1280 x 720", "1600 x 900", "1920 x 1080", "2560 x 1440", "3840 x 2160"]
# What the resolution menu shows for a typed size that is none of the presets.
RESOLUTION_CUSTOM = "Custom"
REFRESH_RATE_PRESETS = ["60", "90", "120", "144", "165", "240"]

# This project's known-good baseline profile, auto-detect scales density
# relative to this, not to any fixed Android density bucket, since the
# goal is "looks the same as it does at 1920x1080@240dpi," not matching a
# real handheld device's physical DPI.
REFERENCE_DISPLAY = {"width": 1920, "height": 1080, "density": 240}


def field_width(widget, sample: str) -> int:
    """Wide enough for sample in widget's own font, plus the field's padding."""
    return widget.fontMetrics().horizontalAdvance(sample) + 24


def dim_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color: {TEXT_DIM};")
    return label


class DisplayPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header(
            "Display",
            "The size iiSU's screen is drawn at. It only affects iiSU itself: games run at your "
            "monitor's own resolution. A change makes the next start a full restart.",
        )

        # Fields on the left in a tight form, the preview right beside them;
        # widths come from the font so "1920" or "2160" never clips, even on
        # a scaled display, and nothing is stretched across empty space.
        columns = QHBoxLayout()
        columns.setSpacing(40)
        settings_col = QVBoxLayout()
        settings_col.setSpacing(14)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        grid.addWidget(QLabel("Resolution"), 0, 0)
        resolution_row = QHBoxLayout()
        resolution_row.setSpacing(8)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTION_PRESETS + [RESOLUTION_CUSTOM])
        self.resolution_combo.setCurrentIndex(self.resolution_combo.findText(RESOLUTION_CUSTOM))
        self.resolution_combo.currentTextChanged.connect(self._apply_resolution_preset)
        self.resolution_combo.setMinimumWidth(field_width(self.resolution_combo, "3840 x 2160") + 28)
        resolution_row.addWidget(self.resolution_combo)
        self.width_edit = QLineEdit()
        self.width_edit.setFixedWidth(field_width(self.width_edit, "00000"))
        self.width_edit.setAccessibleName("Width in pixels")
        resolution_row.addWidget(self.width_edit)
        resolution_row.addWidget(QLabel("x"))
        self.height_edit = QLineEdit()
        self.height_edit.setFixedWidth(field_width(self.height_edit, "00000"))
        self.height_edit.setAccessibleName("Height in pixels")
        resolution_row.addWidget(self.height_edit)
        resolution_row.addStretch(1)
        grid.addLayout(resolution_row, 0, 1)

        density_label = QLabel("Text and icon size")
        density_label.setToolTip("Higher makes iiSU's text and icons bigger. 240 suits 1080p, 320 suits 1440p, 480 suits 4K.")
        grid.addWidget(density_label, 1, 0)
        density_row = QHBoxLayout()
        density_row.setSpacing(8)
        self.density_edit = QLineEdit()
        self.density_edit.setFixedWidth(field_width(self.density_edit, "0000"))
        self.density_edit.setToolTip(density_label.toolTip())
        density_row.addWidget(self.density_edit)
        density_row.addWidget(dim_label("dpi"))
        density_row.addStretch(1)
        grid.addLayout(density_row, 1, 1)

        grid.addWidget(QLabel("Refresh rate"), 2, 0)
        # One editable box with the usual rates in its menu, in place of a
        # field plus a separate preset menu. Its line edit keeps the
        # refresh_edit name the rest of this page reads and writes.
        refresh_row = QHBoxLayout()
        refresh_row.setSpacing(8)
        refresh_combo = QComboBox()
        refresh_combo.setEditable(True)
        refresh_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        refresh_combo.addItems(REFRESH_RATE_PRESETS)
        refresh_combo.setFixedWidth(field_width(refresh_combo, "0000") + 28)
        self.refresh_edit = refresh_combo.lineEdit()
        refresh_row.addWidget(refresh_combo)
        refresh_row.addWidget(dim_label("Hz"))
        refresh_row.addStretch(1)
        grid.addLayout(refresh_row, 2, 1)
        settings_col.addLayout(grid)

        redetect_button = QPushButton("Auto-detect from my monitor")
        redetect_button.setObjectName("ghost")
        redetect_button.clicked.connect(self._autodetect_display)
        settings_col.addWidget(redetect_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.fullscreen_check = QCheckBox("Show iiSU fullscreen")
        settings_col.addWidget(self.fullscreen_check)
        settings_col.addStretch(1)

        preview_col = QVBoxLayout()
        preview_col.setSpacing(6)
        self.preview = DisplayPreview(box_width=220, box_height=150)
        preview_col.addWidget(self.preview)
        preview_col.addWidget(dim_label("Shape of iiSU's screen"), 0, Qt.AlignmentFlag.AlignHCenter)
        preview_col.addStretch(1)

        columns.addLayout(settings_col)
        columns.addLayout(preview_col)
        columns.addStretch(1)
        self.body_layout.addLayout(columns)
        self.body_layout.addStretch(1)

        self.width_edit.textChanged.connect(self._redraw_preview)
        self.height_edit.textChanged.connect(self._redraw_preview)

        self.reload_from_config()

    def reload_from_config(self) -> None:
        display = self.window.config_data.get("display", {"width": 1920, "height": 1080, "density": 240, "refresh_rate": 60})
        self.width_edit.setText(str(display.get("width", 1920)))
        self.height_edit.setText(str(display.get("height", 1080)))
        self.density_edit.setText(str(display.get("density", 240)))
        self.refresh_edit.setText(str(display.get("refresh_rate", 60)))
        self.fullscreen_check.setChecked(bool(self.window.config_data.get("iisu_fullscreen", True)))
        self._redraw_preview()

    def get_display(self) -> dict | None:
        try:
            return {
                "width": int(self.width_edit.text()),
                "height": int(self.height_edit.text()),
                "density": int(self.density_edit.text()),
                "refresh_rate": int(self.refresh_edit.text()),
                # Moved to the VM Profiles page; carried through
                # untouched so an older config.json keeps its value.
                "gpu_mode": self.window.config_data.get("display", {}).get("gpu_mode", "auto"),
            }
        except ValueError:
            return None

    def get_fullscreen(self) -> bool:
        return self.fullscreen_check.isChecked()

    def _apply_resolution_preset(self, text: str) -> None:
        # "Custom" changes nothing: the width and height boxes are the custom size.
        if text not in RESOLUTION_PRESETS:
            return
        width, height = (part.strip() for part in text.split("x"))
        self.width_edit.setText(width)
        self.height_edit.setText(height)

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

    def _autodetect_display(self) -> None:
        try:
            width, height, hz = winapi.get_primary_monitor_mode()
        except Exception as e:
            QMessageBox.critical(self, "Couldn't detect monitor", str(e))
            return
        self.width_edit.setText(str(width))
        self.height_edit.setText(str(height))
        self.refresh_edit.setText(str(hz))
        # Density scales with resolution rather than staying fixed:
        # Android's own UI sizing is density-driven, so jumping resolution while density stayed fixed
        # made everything render tiny on higher-resolution displays.
        density = round(REFERENCE_DISPLAY["density"] * height / REFERENCE_DISPLAY["height"])
        self.density_edit.setText(str(density))
