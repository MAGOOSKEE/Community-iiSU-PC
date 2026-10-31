"""Makes a user repatch iiSU after a project update changed the patch
(shared/iisu_install_record.PATCH_VERSION), before iiSU can be started again.
The Manager opens it at launch, and Home's Start button turns into "Repatch
iiSU" until it's done, so nobody runs a VM whose iiSU predates the bridge it
talks to.

The repatch itself is the same one the Updates page runs (IisuUpdateDialog),
from the APK iiSU was last installed from, the latest official release, or a
file picked by hand. It installs over the existing iiSU, so its data stays.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QVBoxLayout,
)

from bridge.services import iisu_update_service as svc
from bridge.ui.dialogs.iisu_update_dialog import IisuUpdateDialog
from bridge.ui.workers.task_runner import run_in_background
from shared import iisu_install_record
from shared.qt_theme import Fonts, RED, SPACING_MD, SPACING_SM


class RepatchWizard(QDialog):
    def __init__(self, vm_running, parent=None):
        """vm_running() says whether the VM is up right now: the repatch boots
        it on its own to install, so it has to be stopped first."""
        super().__init__(parent)
        self.setWindowTitle("Repatch iiSU")
        self.resize(560, 0)
        self._vm_running = vm_running
        self._picked_apk: Path | None = None
        self._latest = None
        self.repatched = False

        record = iisu_install_record.load()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        heading = QLabel("iiSU needs to be repatched")
        heading.setFont(Fonts.heading())
        layout.addWidget(heading)

        intro = QLabel(
            "This update of Community-iiSU-PC changes the patch it applies to iiSU. Repatch it "
            "to keep using it: iiSU can't be started until then. Your iiSU data, games and "
            "settings stay as they are."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        changes = QLabel("\n".join(f"• {change}" for change in iisu_install_record.changes_since(record)))
        changes.setWordWrap(True)
        changes.setProperty("role", "dim")
        layout.addWidget(changes)

        self.steps = iisu_install_record.steps_since(record)
        if self.steps:
            layout.addSpacing(SPACING_SM)
            layout.addWidget(QLabel("After repatching, change this in iiSU:"))
            steps = QLabel(self.steps_text())
            steps.setWordWrap(True)
            layout.addWidget(steps)

        layout.addSpacing(SPACING_SM)
        layout.addWidget(QLabel("Repatch from:"))
        self._sources = QButtonGroup(self)

        self._recorded_apk = svc.find_recorded_apk(record)
        self.recorded_option = QRadioButton(
            f"The APK iiSU was installed from: {self._recorded_apk.name}" if self._recorded_apk
            else "The APK iiSU was installed from (not found in installer/input or Downloads)"
        )
        self.recorded_option.setEnabled(self._recorded_apk is not None)
        self._sources.addButton(self.recorded_option)
        layout.addWidget(self.recorded_option)

        self.latest_option = QRadioButton("The latest official iiSU release (checking GitHub...)")
        self.latest_option.setEnabled(False)
        self._sources.addButton(self.latest_option)
        layout.addWidget(self.latest_option)

        pick_row = QHBoxLayout()
        self.picked_option = QRadioButton("An APK file:")
        self._sources.addButton(self.picked_option)
        pick_row.addWidget(self.picked_option)
        self.pick_button = QPushButton("Choose...")
        self.pick_button.clicked.connect(self._pick_apk)
        pick_row.addWidget(self.pick_button)
        pick_row.addStretch(1)
        layout.addLayout(pick_row)

        (self.recorded_option if self._recorded_apk else self.picked_option).setChecked(True)
        self._sources.buttonToggled.connect(lambda *_: self._refresh())

        self.running_label = QLabel("Stop iiSU on the Home page first: repatching starts the VM on its own to install.")
        self.running_label.setWordWrap(True)
        self.running_label.setStyleSheet(f"color: {RED};")
        layout.addWidget(self.running_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        later = QPushButton("Not Now")
        later.clicked.connect(self.reject)
        buttons.addWidget(later)
        self.repatch_button = QPushButton("Repatch iiSU")
        self.repatch_button.setObjectName("accent")
        self.repatch_button.clicked.connect(self._repatch)
        buttons.addWidget(self.repatch_button)
        layout.addLayout(buttons)

        self._latest_signals = run_in_background(self._fetch_latest, self._on_latest, self._on_latest_failed)
        self._refresh()

    @staticmethod
    def _fetch_latest():
        return svc._fetch_latest_official_release()

    def _on_latest(self, latest) -> None:
        self._latest = latest
        if latest is None:
            self.latest_option.setText("The latest official iiSU release (none with an APK is published)")
            return
        self.latest_option.setText(f"The latest official iiSU release: {latest.version} ({latest.asset_name})")
        self.latest_option.setEnabled(True)
        if not self._recorded_apk and self._picked_apk is None:
            self.latest_option.setChecked(True)
        self._refresh()

    def _on_latest_failed(self, _message: str) -> None:
        self.latest_option.setText("The latest official iiSU release (couldn't reach GitHub)")

    def _pick_apk(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select an iiSU APK", "", "Android APK (*.apk)")
        if path:
            self._picked_apk = Path(path)
            self.picked_option.setText(f"An APK file: {self._picked_apk.name}")
            self.picked_option.setChecked(True)
        self._refresh()

    def selected_source(self):
        """What apply_iisu_update() takes: a local APK Path, or the release's
        download URL as a str. None when the chosen option has nothing yet."""
        if self.recorded_option.isChecked():
            return self._recorded_apk
        if self.latest_option.isChecked():
            return self._latest.download_url if self._latest else None
        return self._picked_apk

    def _refresh(self) -> None:
        running = bool(self._vm_running())
        self.running_label.setVisible(running)
        self.repatch_button.setEnabled(not running and self.selected_source() is not None)

    def _repatch(self) -> None:
        self._refresh()
        source = self.selected_source()
        if source is None or not self.repatch_button.isEnabled():
            return
        dialog = IisuUpdateDialog(source, parent=self)
        dialog.start()
        dialog.exec()
        if dialog.succeeded:
            self.repatched = True
            if self.steps:
                QMessageBox.information(
                    self, "One more step",
                    "iiSU is repatched. Start it and change this so everything works:\n\n" + self.steps_text(),
                )
            self.accept()

    def steps_text(self) -> str:
        return "\n".join(f"• {step}" for step in self.steps)
