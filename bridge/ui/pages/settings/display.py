"""Display settings page -- ports manager.py's _build_display_page. Reuses
DisplayPreview (bridge/ui/widgets/display_preview.py), the same widget
onboarding_wizard.py's DisplayStep uses, instead of a second hand-drawn
aspect-ratio canvas."""

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

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
REFRESH_RATE_PRESETS = ["60", "90", "120", "144", "165", "240"]
GPU_MODE_PRESETS = ["auto", "host", "swiftshader_indirect", "angle_indirect"]

# This project's known-good baseline profile -- auto-detect scales density
# relative to this, not to any fixed Android density bucket, since the
# goal is "looks the same as it does at 1920x1080@240dpi," not matching a
# real handheld device's physical DPI.
REFERENCE_DISPLAY = {"width": 1920, "height": 1080, "density": 240}


class DisplayPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window

        self.add_header("Display", "The emulated device's actual hardware profile -- applying it cold-boots the AVD.")

        columns = QHBoxLayout()
        settings_col = QVBoxLayout()
        grid = QGridLayout()

        grid.addWidget(QLabel("Resolution:"), 0, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTION_PRESETS)
        self.resolution_combo.setCurrentIndex(-1)
        self.resolution_combo.currentTextChanged.connect(self._apply_resolution_preset)
        grid.addWidget(self.resolution_combo, 0, 1)

        grid.addWidget(QLabel("or exactly:"), 1, 0)
        exact_row = QHBoxLayout()
        self.width_edit = QLineEdit()
        self.width_edit.setFixedWidth(60)
        exact_row.addWidget(self.width_edit)
        exact_row.addWidget(QLabel("x"))
        self.height_edit = QLineEdit()
        self.height_edit.setFixedWidth(60)
        exact_row.addWidget(self.height_edit)
        exact_row.addStretch(1)
        grid.addLayout(exact_row, 1, 1)

        grid.addWidget(QLabel("Density (dpi):"), 2, 0)
        self.density_edit = QLineEdit()
        self.density_edit.setFixedWidth(70)
        grid.addWidget(self.density_edit, 2, 1)

        grid.addWidget(QLabel("Refresh rate (Hz):"), 3, 0)
        refresh_row = QHBoxLayout()
        self.refresh_edit = QLineEdit()
        self.refresh_edit.setFixedWidth(50)
        refresh_row.addWidget(self.refresh_edit)
        refresh_combo = QComboBox()
        refresh_combo.addItems(REFRESH_RATE_PRESETS)
        refresh_combo.setCurrentIndex(-1)
        refresh_combo.currentTextChanged.connect(lambda text: self.refresh_edit.setText(text) if text else None)
        refresh_row.addWidget(refresh_combo)
        refresh_row.addStretch(1)
        grid.addLayout(refresh_row, 3, 1)

        grid.addWidget(QLabel("GPU rendering:"), 4, 0)
        self.gpu_combo = QComboBox()
        self.gpu_combo.addItems(GPU_MODE_PRESETS)
        grid.addWidget(self.gpu_combo, 4, 1)

        gpu_note = QLabel(
            'Try "host" or "swiftshader_indirect" here if you see screen tearing\n'
            "or audio cutting out after tabbing away and back, a known Android\n"
            'Emulator GPU-backend issue on some hardware. "auto" is the default.'
        )
        gpu_note.setStyleSheet(f"color: {TEXT_DIM};")
        grid.addWidget(gpu_note, 5, 0, 1, 2)

        settings_col.addLayout(grid)
        redetect_button = QPushButton("Auto-detect from primary monitor")
        redetect_button.setObjectName("ghost")
        redetect_button.clicked.connect(self._autodetect_display)
        settings_col.addWidget(redetect_button)
        settings_col.addStretch(1)

        preview_col = QVBoxLayout()
        preview_col.addWidget(QLabel("Aspect ratio preview"))
        self.preview = DisplayPreview(box_width=150, box_height=110)
        preview_col.addWidget(self.preview)
        preview_col.addStretch(1)

        columns.addLayout(settings_col, 1)
        columns.addLayout(preview_col)
        self.body_layout.addLayout(columns)

        note = QLabel(
            "Only affects iiSU's own UI smoothness inside the AVD. Actual gameplay runs\n"
            "in a separate native Windows emulator process, which already uses your\n"
            "monitor's real refresh rate with no setup needed."
        )
        note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(note)

        self.fullscreen_check = QCheckBox("Maximize the iiSU/AVD window automatically")
        self.body_layout.addWidget(self.fullscreen_check)

        self.body_layout.addWidget(QLabel("AVD name:"))
        self.avd_name_edit = QLineEdit()
        self.avd_name_edit.setFixedWidth(200)
        self.body_layout.addWidget(self.avd_name_edit)
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
        self.gpu_combo.setCurrentText(display.get("gpu_mode", "auto"))
        self.fullscreen_check.setChecked(bool(self.window.config_data.get("iisu_fullscreen", True)))
        self.avd_name_edit.setText(self.window.config_data.get("avd_name", "iisuwin"))
        self._redraw_preview()

    def get_display(self) -> dict | None:
        try:
            return {
                "width": int(self.width_edit.text()),
                "height": int(self.height_edit.text()),
                "density": int(self.density_edit.text()),
                "refresh_rate": int(self.refresh_edit.text()),
                "gpu_mode": self.gpu_combo.currentText(),
            }
        except ValueError:
            return None

    def get_fullscreen(self) -> bool:
        return self.fullscreen_check.isChecked()

    def get_avd_name(self) -> str:
        return self.avd_name_edit.text().strip() or "iisuwin"

    def _apply_resolution_preset(self, text: str) -> None:
        if "x" not in text:
            return
        width, height = (part.strip() for part in text.split("x"))
        self.width_edit.setText(width)
        self.height_edit.setText(height)

    def _redraw_preview(self, *_args) -> None:
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
        # Density scales with resolution rather than staying fixed -- see
        # manager.py's original comment: Android's own UI sizing is
        # density-driven, so jumping resolution while density stayed fixed
        # made everything render tiny on higher-resolution displays.
        density = round(REFERENCE_DISPLAY["density"] * height / REFERENCE_DISPLAY["height"])
        self.density_edit.setText(str(density))
