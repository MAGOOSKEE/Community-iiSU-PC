"""Asks which icon the desktop shortcuts and the Manager's window show:
iiSU's own, extracted from your iiSU APK (see create_shortcut.py), or this
project's iiPC icon. Opened when onboarding finishes, right after Setup made
the shortcuts, and by the Home page's Recreate Shortcut. The caller saves
the choice as config.json's "icon_choice" and remakes the shortcuts.
Onboarding's also asks whether to keep the desktop shortcut Setup made."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout,
)

import create_shortcut
from shared.qt_theme import Fonts, SPACING_LG, SPACING_MD, SPACING_SM

PREVIEW_SIZE = 160


class IconChoiceDialog(QDialog):
    def __init__(self, current: str | None, parent=None, offer_no_shortcut: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Shortcut Icon")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("Choose your shortcut icon")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)
        intro = QLabel(
            "Used by the desktop shortcuts and the Manager's window. Change it any time with "
            "Recreate Shortcut, in the menu next to Start on the Home page."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "dim")
        layout.addWidget(intro)

        self._choices = QButtonGroup(self)
        options = QHBoxLayout()
        options.setSpacing(SPACING_LG)
        iisu_png = create_shortcut.EXTRACTED_PNG_PATH
        self.iisu_option = self._add_option(
            options, "iiSU's own icon" if iisu_png.is_file() else "iiSU's own icon (not extracted from your APK yet)",
            iisu_png if iisu_png.is_file() else None,
        )
        self.iisu_option.setEnabled(iisu_png.is_file())
        self.iipc_option = self._add_option(options, "Community-iiSU-PC's iiPC icon", create_shortcut.IIPC_PNG_PATH)
        layout.addLayout(options)

        use_iipc = current == create_shortcut.ICON_IIPC or not self.iisu_option.isEnabled()
        (self.iipc_option if use_iipc else self.iisu_option).setChecked(True)

        self.shortcut_check = QCheckBox("Put a Community-iiSU-PC shortcut on the desktop")
        self.shortcut_check.setChecked(True)
        self.shortcut_check.setVisible(offer_no_shortcut)
        layout.addSpacing(SPACING_SM)
        layout.addWidget(self.shortcut_check)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        use = QPushButton("Use This Icon")
        use.setObjectName("accent")
        use.setDefault(True)
        use.clicked.connect(self.accept)
        buttons.addWidget(use)
        layout.addLayout(buttons)

    def _add_option(self, row: QHBoxLayout, label: str, image) -> QRadioButton:
        column = QVBoxLayout()
        preview = QLabel()
        preview.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(image)) if image is not None else QPixmap()
        if pixmap.isNull():
            preview.setText("No preview")
            preview.setProperty("role", "dim")
        else:
            preview.setPixmap(pixmap.scaled(
                PREVIEW_SIZE, PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))
        column.addWidget(preview, 0, Qt.AlignmentFlag.AlignHCenter)
        option = QRadioButton(label)
        self._choices.addButton(option)
        column.addWidget(option, 0, Qt.AlignmentFlag.AlignHCenter)
        # The picture picks it too.
        preview.mousePressEvent = lambda _event: option.setChecked(True) if option.isEnabled() else None
        row.addLayout(column)
        return option

    def choice(self) -> str:
        return create_shortcut.ICON_IIPC if self.iipc_option.isChecked() else create_shortcut.ICON_IISU

    def wants_shortcut(self) -> bool:
        return self.shortcut_check.isChecked()
