"""Emulator settings page: named launch profiles for the Android SDK
emulator, so audio crackle, tearing, or slow UI can be A/B tested by
switching profiles and restarting. Backed by bridge/emulator_profiles.py.

The profile selected here is the one the next start uses. Profiles only
cover launch-time knobs on the existing AVD (GPU backend, acceleration,
cores, RAM, audio devices, extra flags and environment), not which system
image it runs.
"""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

import copy

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

import emulator_profiles as ep
from bridge.ui.pages.base import PageBase
from shared.qt_theme import TEXT_DIM
from shared.vm_backend import BACKEND_WAYDROID, resolve_backend

AUDIO_LABELS = {
    "default": "Default (microphone and speakers)",
    "no_input": "Speakers only (detach the microphone)",
    "none": "No audio at all (diagnostic)",
}
ACCEL_LABELS = {"auto": "Automatic", "on": "Force on", "off": "Off (very slow)"}


class EmulatorPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window
        self._active = ep.DEFAULT_PROFILE_NAME
        self._profiles: dict[str, dict] = {}
        self._loading = False

        self.add_header(
            "Emulator",
            "Named launch profiles for the Android emulator. Pick one, Save, then Open to test it.",
        )

        self.waydroid_note = QLabel(
            "This install runs on Waydroid, which has no emulator launch options. Profiles are saved but unused."
        )
        self.waydroid_note.setStyleSheet(f"color: {TEXT_DIM};")
        self.waydroid_note.setWordWrap(True)
        self.body_layout.addWidget(self.waydroid_note)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(260)
        self.profile_combo.currentTextChanged.connect(self._on_profile_selected)
        profile_row.addWidget(self.profile_combo)
        for text, handler in (
            ("Duplicate", self._duplicate_profile),
            ("Rename", self._rename_profile),
            ("Delete", self._delete_profile),
        ):
            button = QPushButton(text)
            button.setObjectName("ghost")
            button.clicked.connect(handler)
            profile_row.addWidget(button)
        profile_row.addStretch(1)
        self.body_layout.addLayout(profile_row)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Add from preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(ep.PRESETS))
        preset_row.addWidget(self.preset_combo)
        add_preset = QPushButton("Add")
        add_preset.setObjectName("ghost")
        add_preset.clicked.connect(self._add_preset)
        preset_row.addWidget(add_preset)
        preset_row.addStretch(1)
        self.body_layout.addLayout(preset_row)

        grid = QGridLayout()
        row = 0

        grid.addWidget(QLabel("GPU rendering:"), row, 0)
        self.gpu_combo = QComboBox()
        self.gpu_combo.addItems(ep.GPU_MODES)
        grid.addWidget(self.gpu_combo, row, 1)
        row += 1

        grid.addWidget(QLabel("Hardware acceleration:"), row, 0)
        self.accel_combo = QComboBox()
        for key in ep.ACCEL_MODES:
            self.accel_combo.addItem(ACCEL_LABELS[key], key)
        grid.addWidget(self.accel_combo, row, 1)
        row += 1

        grid.addWidget(QLabel("CPU cores:"), row, 0)
        self.cores_spin = QSpinBox()
        self.cores_spin.setRange(0, 32)
        self.cores_spin.setSpecialValueText("AVD default")
        grid.addWidget(self.cores_spin, row, 1)
        row += 1

        grid.addWidget(QLabel("RAM (MB):"), row, 0)
        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(0, 65536)
        self.ram_spin.setSingleStep(512)
        self.ram_spin.setSpecialValueText("AVD default")
        grid.addWidget(self.ram_spin, row, 1)
        row += 1

        grid.addWidget(QLabel("Audio:"), row, 0)
        self.audio_combo = QComboBox()
        for key in ep.AUDIO_MODES:
            self.audio_combo.addItem(AUDIO_LABELS[key], key)
        grid.addWidget(self.audio_combo, row, 1)
        row += 1

        grid.addWidget(QLabel("Extra emulator flags:"), row, 0)
        self.extra_args_edit = QLineEdit()
        self.extra_args_edit.setPlaceholderText("e.g. -feature -Vulkan")
        grid.addWidget(self.extra_args_edit, row, 1)
        row += 1

        grid.addWidget(QLabel("Environment variables:"), row, 0)
        self.env_edit = QPlainTextEdit()
        self.env_edit.setPlaceholderText("One KEY=VALUE per line")
        self.env_edit.setFixedHeight(70)
        grid.addWidget(self.env_edit, row, 1)

        grid.setColumnStretch(1, 1)
        self.body_layout.addLayout(grid)

        note = QLabel(
            "Testing audio problems: duplicate a profile, change one thing (try \"Speakers only\", then\n"
            "\"No audio at all\" to confirm audio is the cause, then a different GPU rendering mode),\n"
            "Save, Stop, Open. A profile change cold-boots the VM once. The Display page's GPU option\n"
            "moved here."
        )
        note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(note)
        self.body_layout.addStretch(1)

        for signal in (
            self.gpu_combo.currentIndexChanged,
            self.accel_combo.currentIndexChanged,
            self.cores_spin.valueChanged,
            self.ram_spin.valueChanged,
            self.audio_combo.currentIndexChanged,
            self.extra_args_edit.textChanged,
            self.env_edit.textChanged,
        ):
            signal.connect(self._store_fields)

        self.reload_from_config()

    # == Config <-> fields ==

    def reload_from_config(self) -> None:
        self._active, self._profiles = ep.get_profiles(self.window.config_data)
        self._profiles = copy.deepcopy(self._profiles)
        self.waydroid_note.setVisible(resolve_backend(self.window.config_data) == BACKEND_WAYDROID)
        self._refresh_combo()

    def get_emulator_profiles(self) -> dict:
        """The "emulator_profiles" block exactly as Save would write it."""
        return ep.with_profiles({}, self._active, self._profiles)["emulator_profiles"]

    def _refresh_combo(self) -> None:
        self._loading = True
        self.profile_combo.clear()
        self.profile_combo.addItems(list(self._profiles))
        self.profile_combo.setCurrentText(self._active)
        self._loading = False
        self._load_fields(self._profiles[self._active])

    def _load_fields(self, profile: dict) -> None:
        self._loading = True
        self.gpu_combo.setCurrentText(profile["gpu_mode"])
        self.accel_combo.setCurrentIndex(max(0, self.accel_combo.findData(profile["accel"])))
        self.cores_spin.setValue(profile["cores"])
        self.ram_spin.setValue(profile["ram_mb"])
        self.audio_combo.setCurrentIndex(max(0, self.audio_combo.findData(profile["audio"])))
        self.extra_args_edit.setText(profile["extra_args"])
        self.env_edit.setPlainText(profile["env"])
        self._loading = False

    def _store_fields(self, *_args) -> None:
        if self._loading or self._active not in self._profiles:
            return
        self._profiles[self._active] = ep.normalize_profile({
            "gpu_mode": self.gpu_combo.currentText(),
            "accel": self.accel_combo.currentData(),
            "cores": self.cores_spin.value(),
            "ram_mb": self.ram_spin.value(),
            "audio": self.audio_combo.currentData(),
            "extra_args": self.extra_args_edit.text(),
            "env": self.env_edit.toPlainText(),
        })

    def _on_profile_selected(self, name: str) -> None:
        if self._loading or name not in self._profiles:
            return
        self._active = name
        self._load_fields(self._profiles[name])

    # == Profile management ==

    def _ask_name(self, title: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, "Profile name:", text=default)
        name = name.strip()
        if not ok or not name:
            return None
        if name in self._profiles:
            QMessageBox.warning(self, title, f'A profile named "{name}" already exists.')
            return None
        return name

    def _unique_name(self, base: str) -> str:
        if base not in self._profiles:
            return base
        index = 2
        while f"{base} {index}" in self._profiles:
            index += 1
        return f"{base} {index}"

    def _duplicate_profile(self) -> None:
        name = self._ask_name("Duplicate profile", self._unique_name(f"{self._active} copy"))
        if name is None:
            return
        self._profiles[name] = copy.deepcopy(self._profiles[self._active])
        self._active = name
        self._refresh_combo()

    def _rename_profile(self) -> None:
        name = self._ask_name("Rename profile", self._active)
        if name is None:
            return
        self._profiles = {(name if key == self._active else key): value for key, value in self._profiles.items()}
        self._active = name
        self._refresh_combo()

    def _delete_profile(self) -> None:
        if len(self._profiles) <= 1:
            QMessageBox.information(self, "Delete profile", "At least one profile has to exist.")
            return
        del self._profiles[self._active]
        self._active = next(iter(self._profiles))
        self._refresh_combo()

    def _add_preset(self) -> None:
        preset_name = self.preset_combo.currentText()
        name = self._unique_name(preset_name)
        self._profiles[name] = ep.normalize_profile(ep.PRESETS[preset_name])
        self._active = name
        self._refresh_combo()
