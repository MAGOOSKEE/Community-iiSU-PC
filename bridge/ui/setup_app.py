"""
GUI front-end for installer/setup_wizard.py.

Lets you pick your iiSU APK, then runs the whole first-time setup (SDK/AVD
bootstrap, patching, install) on a background thread while streaming its
progress into a log view. All the actual work lives in setup_wizard.py /
sdk_bootstrap.py / patch_iisu.py, this is purely a front end for it.

Stays its own separate window rather than a page inside the Manager, since
a one-time install wizard is a different shape of problem than the
settings the Manager's sidebar covers afterward.
"""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import iisu_release
import setup_wizard

# PySide6 is this file's own GUI toolkit, has to be confirmed installed
# before the imports below, which need it, not inside main().
setup_wizard.ensure_pyside6()

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

import winapi
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.gradient_divider import GradientDivider
from bridge.ui.widgets.step_checklist import StepChecklist
from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
from shared import vm_storage
from shared.qt_theme import Fonts, GREEN, RED, SPACING_LG, SPACING_MD, SPACING_SM, apply_theme

BRIDGE_DIR = setup_wizard.BRIDGE_DIR

DOWNLOAD_STAGE = "Downloading the latest iiSU release"

# How much taller the window grows while the log is shown.
LOG_HEIGHT = 280

# What to expect from the slow steps, shown beside them while they run.
# Keyed by the names shown (STAGE_NAMES' values).
STAGE_HINTS = {
    DOWNLOAD_STAGE: "about 130 MB",
    "Downloading and setting up Android": "the long one: about 3 GB",
    "Patching iiSU": "a minute or two",
    "Moving Android into this folder": "a few minutes",
    "Installing iiSU in Android": "starts Android, several minutes",
}

# Plainer names for Setup's own steps (installer/setup_wizard.py's
# SETUP_STAGES); the log keeps the originals.
STAGE_NAMES = {
    "Setting up the Android SDK and AVD": "Downloading and setting up Android",
    "Setting up Android tooling": "Setting up Android tools",
    "Preparing the signing key": "Preparing to sign iiSU",
    "Copying to the portable AVD": "Moving Android into this folder",
    "Installing iiSU and redirector stubs": "Installing iiSU in Android",
}


class SetupWindow(QMainWindow):
    stage_changed = Signal(str, int, int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Community-iiSU-PC Setup")
        self.setMinimumWidth(640)

        self.apk_path: Path | None = None
        self.running = False
        self._has_run = False
        self._onboarding_process: subprocess.Popen | None = None

        self._log_stream = LogStreamRedirector()
        self._log_stream.text_written.connect(self._append_log)
        self.stage_changed.connect(self._apply_stage)

        self._build_ui()
        # Just tall enough for what's on screen; Show details adds room for the log.
        self.resize(760, self._compact_height())
        self._autodetect_apk()

    # == UI ==

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(SPACING_LG - 4, 18, SPACING_LG - 4, SPACING_SM)
        root.setSpacing(SPACING_SM)

        title = QLabel("Community-iiSU-PC Setup")
        title.setFont(Fonts.title())
        root.addWidget(title)

        subtitle = QLabel(
            "Patches your copy of iiSU to hand game launches to PC emulators,\n"
            "and sets up a self-contained Android VM to run it in."
        )
        subtitle.setFont(Fonts.body())
        subtitle.setProperty("role", "dim")
        root.addWidget(subtitle)

        root.addSpacing(6)
        # Also the progress bar: it fills step by step once Setup starts.
        self.progress_line = GradientDivider(height=4)
        root.addWidget(self.progress_line)
        root.addSpacing(10)

        apk_card = Card()
        apk_layout = QVBoxLayout(apk_card)
        apk_layout.setContentsMargins(SPACING_MD, SPACING_MD - 2, SPACING_MD, SPACING_MD - 2)
        apk_heading = QLabel("iiSU APK")
        apk_heading.setFont(Fonts.heading())
        apk_layout.addWidget(apk_heading)

        self.latest_radio = QRadioButton("Download the latest official release (from iiSU's GitHub)")
        self.latest_radio.setToolTip(
            "Fetches the newest non-prerelease APK from github.com/iisu-network/iiSU on your machine "
            "and checks it against GitHub's SHA-256. Nothing is bundled with this project."
        )
        self.custom_radio = QRadioButton("Use my own APK (custom build or pre-release)")
        self.latest_radio.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.latest_radio)
        mode_group.addButton(self.custom_radio)
        self.latest_radio.toggled.connect(self._update_mode)
        apk_layout.addWidget(self.latest_radio)
        apk_layout.addWidget(self.custom_radio)

        apk_row = QHBoxLayout()
        self.apk_label = QLabel("No APK selected.")
        self.apk_label.setProperty("role", "dim")
        apk_row.addSpacing(22)
        apk_row.addWidget(self.apk_label, stretch=1)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self._browse_apk)
        apk_row.addWidget(self.browse_button)
        apk_layout.addLayout(apk_row)
        root.addWidget(apk_card)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("Start Setup")
        self.start_button.setObjectName("accent")
        self.start_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.start_button.clicked.connect(self._start_setup)
        action_row.addWidget(self.start_button)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self.steps_card = Card()
        steps_layout = QVBoxLayout(self.steps_card)
        steps_layout.setContentsMargins(SPACING_MD, SPACING_MD - 2, SPACING_MD, SPACING_MD - 2)
        self.checklist = StepChecklist(self._planned_steps(), STAGE_HINTS)
        steps_layout.addWidget(self.checklist)
        self.steps_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        root.addWidget(self.steps_card)


        details_row = QHBoxLayout()
        self.details_button = QPushButton("Show details")
        self.details_button.setObjectName("ghost")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._set_details_shown)
        details_row.addWidget(self.details_button)
        details_row.addStretch(1)
        root.addLayout(details_row)

        self.log_card = Card()
        log_layout = QVBoxLayout(self.log_card)
        log_layout.setContentsMargins(SPACING_SM + 2, SPACING_SM + 2, SPACING_SM + 2, SPACING_SM + 2)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        log_layout.addWidget(self.log_text)
        self.log_text.setMinimumHeight(LOG_HEIGHT - 40)
        root.addWidget(self.log_card, stretch=1)
        self.log_card.setVisible(False)

        self._update_mode()
        self.status_label = QLabel("Ready.")
        self.status_label.setProperty("role", "dim")
        root.addWidget(self.status_label)

    def _planned_steps(self) -> list[str]:
        """Setup's steps in the order they'll run, in plain words, with the
        download first when Setup is fetching iiSU itself."""
        backend = setup_wizard.preferred_backend()
        stages = setup_wizard.WAYDROID_SETUP_STAGES if backend == setup_wizard.BACKEND_WAYDROID else setup_wizard.SETUP_STAGES
        names = [STAGE_NAMES.get(stage, stage) for stage in stages]
        if getattr(self, "latest_radio", None) is not None and self.latest_radio.isChecked():
            names.insert(0, STAGE_NAMES.get(DOWNLOAD_STAGE, DOWNLOAD_STAGE))
        return names

    def _compact_height(self) -> int:
        """The window height that fits everything but the log."""
        layout = self.centralWidget().layout()
        layout.activate()
        hidden = self.log_card.isHidden()
        self.log_card.hide()
        layout.activate()
        height = self.centralWidget().sizeHint().height()
        self.log_card.setHidden(hidden)
        return height

    def _fit_height(self) -> None:
        """Grows the window for the log while it's shown, and shrinks it back
        to fit when it's hidden, so there's never an empty band at the bottom."""
        target = self._compact_height() + (LOG_HEIGHT if self.log_card.isVisible() else 0)
        self.setMinimumHeight(self._compact_height())
        self.resize(self.width(), target)

    def _set_details_shown(self, shown: bool) -> None:
        self.log_card.setVisible(shown)
        # After the layout has taken the change in.
        QTimer.singleShot(0, self._fit_height)
        self.details_button.setText("Hide details" if shown else "Show details")

    def _set_status(self, text: str, color: str | None = None) -> None:
        self.status_label.setText(text)
        if color is None:
            self.status_label.setProperty("role", "dim")
            self.status_label.setStyleSheet("")
        else:
            self.status_label.setProperty("role", "")
            self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _autodetect_apk(self) -> None:
        found = setup_wizard.find_input_apk()
        if found is not None:
            self._set_apk(found)
            self.custom_radio.setChecked(True)

    def _update_mode(self) -> None:
        use_latest = self.latest_radio.isChecked()
        # Before the first run only: afterwards the list keeps showing how
        # that run went (which step failed) until Setup starts again.
        if not self.running and not self._has_run and hasattr(self, "checklist"):
            self.checklist.set_labels(self._planned_steps())
            # The download step comes and goes with the APK choice.
            QTimer.singleShot(0, self._fit_height)
        self.browse_button.setEnabled(not use_latest and not self.running)
        self.apk_label.setEnabled(not use_latest)
        if not self.running:
            self.start_button.setEnabled(use_latest or self.apk_path is not None)

    def _browse_apk(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(self, "Select your iiSU APK", "", "Android APK (*.apk)")
        if chosen:
            self._set_apk(Path(chosen))

    def _set_apk(self, path: Path) -> None:
        self.apk_path = path
        self.apk_label.setText(str(path))
        self.apk_label.setProperty("role", "")
        self.apk_label.style().unpolish(self.apk_label)
        self.apk_label.style().polish(self.apk_label)
        self._update_mode()

    # == Log handling ==

    def _append_log(self, text: str) -> None:
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)

    # == Setup run ==

    def _start_setup(self) -> None:
        use_latest = self.latest_radio.isChecked()
        if (self.apk_path is None and not use_latest) or self.running:
            return
        self.running = True
        self.start_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.latest_radio.setEnabled(False)
        self.custom_radio.setEnabled(False)
        self._has_run = True
        self._set_status("Setting up. The first run takes a while; you can leave it running.")

        self.checklist.set_labels(self._planned_steps())
        self.progress_line.set_fraction(0)

        self._setup_signals = run_in_background(
            self._run_setup_thread, self._on_setup_finished,
            apk_path=None if use_latest else self.apk_path,
        )

    def _run_setup_thread(self, apk_path: Path | None) -> Exception | None:
        old_stdout = sys.stdout
        sys.stdout = self._log_stream
        error: Exception | None = None
        try:
            with setup_wizard.session_log():
                try:
                    if apk_path is None:
                        self._on_stage("Downloading the latest iiSU release", 1, 1)
                        apk_path = iisu_release.download_latest(setup_wizard.INPUT_DIR)
                    # Every install gets the lean VM with 16 GB of storage; Settings >
                    # Storage can resize it later.
                    setup_wizard.run_setup(
                        apk_path, on_stage=self._on_stage,
                        storage_gb=vm_storage.DEFAULT_DATA_PARTITION_GB, lean=True,
                    )
                except Exception as e:  # noqa: BLE001; surfaced to the user below, not swallowed
                    error = e
                    print(f"\n[setup] FAILED: {e}\n")
                    print(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
        return error

    def _on_stage(self, label: str, index: int, total: int) -> None:
        # Called from the worker thread, emitting a Qt signal from here is
        # safe the same way LogStreamRedirector's is; stage_changed's queued
        # connection delivers _apply_stage's call on the main thread.
        self.stage_changed.emit(label, index, total)

    def _apply_stage(self, label: str, index: int, total: int) -> None:
        name = STAGE_NAMES.get(label, label)
        labels = self.checklist.labels()
        # Every stage name is on the list; the fallback only guards a stage
        # added to setup_wizard.py without one here.
        row = labels.index(name) if name in labels else min(index - 1, len(labels) - 1)
        self.checklist.start(row)

        self.progress_line.set_fraction(self.checklist.done_count() / max(1, len(labels)))
        self._set_status(f"Step {row + 1} of {len(labels)}: {name}...")

    def _on_setup_finished(self, error: Exception | None) -> None:
        self.running = False
        if error is None:
            self.checklist.finish_all()
            self.progress_line.set_fraction(1.0)
        else:
            self.checklist.fail_current()
            # What went wrong is in the log, so open it.
            self.details_button.setChecked(True)
            self.start_button.setText("Try Again")

        self.latest_radio.setEnabled(True)
        self.custom_radio.setEnabled(True)
        self._update_mode()

        if error is None:
            self._set_status("Setup complete, opening the setup wizard...", GREEN)
            self._open_onboarding()
            # Hiding this window (instead of leaving it open with "next
            # step" buttons) hands off cleanly to onboarding_wizard.py; see
            # _hide_and_wait_for_onboarding for why this process stays
            # alive a while longer than the window does.
            QTimer.singleShot(1200, self._hide_and_wait_for_onboarding)
        else:
            self._set_status(f"Setup failed: {error}", RED)
            if isinstance(error, setup_wizard.WaydroidMissingError) and error.plan is not None:
                self._offer_waydroid_install(error)
            elif isinstance(error, setup_wizard.VirtualizationError):
                self._offer_hypervisor_fix(str(error))
            else:
                QMessageBox.critical(self, "Setup failed", f"{error}\n\nSee the log for details.")

    def _offer_waydroid_install(self, error) -> None:
        """Opt-in only: nothing is installed unless the person says yes here.
        On success Setup resumes by itself, since (unlike the hypervisor and
        kvm-group fixes) nothing needs a restart or re-login afterward."""
        plan = error.plan
        answer = QMessageBox.question(
            self, "Install Waydroid?",
            f"{error}\n\nInstall it now? This runs:\n\n    {plan.display}\n\n"
            "as administrator (expect a permission prompt), then Setup continues.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            QMessageBox.critical(self, "Setup failed", f"{error}\n\nSee the log for details.")
            return
        self.running = True
        self.start_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self._set_status("Installing Waydroid...")
        self._install_signals = run_in_background(self._install_waydroid_thread, self._on_waydroid_installed, plan=plan)

    def _install_waydroid_thread(self, plan) -> Exception | None:
        old_stdout = sys.stdout
        sys.stdout = self._log_stream
        error: Exception | None = None
        try:
            setup_wizard.install_waydroid(plan)
        except Exception as e:  # noqa: BLE001; surfaced to the user below, not swallowed
            error = e
            print(f"\n[setup] FAILED: {e}\n")
        finally:
            sys.stdout = old_stdout
        return error

    def _on_waydroid_installed(self, error: Exception | None) -> None:
        self.running = False
        self.browse_button.setEnabled(True)
        self.start_button.setEnabled(True)
        if error is not None:
            self._set_status(f"Waydroid install failed: {error}", RED)
            QMessageBox.critical(self, "Couldn't install Waydroid", f"{error}\n\nSee the log for details.")
            return
        self._set_status("Waydroid installed, continuing setup...", GREEN)
        self._start_setup()

    def _offer_hypervisor_fix(self, message: str) -> None:
        """VirtualizationError specifically (not every setup failure) means
        there's a concrete, one-click-away fix worth offering right in the
        dialog instead of leaving the person to go search for what
        "Windows Hypervisor Platform" (or, on Linux, the `kvm` group)
        even is."""
        if "Hypervisor Platform" in message:
            self._offer_fix(
                message, title="Enable Windows Hypervisor Platform?",
                confirm=(
                    "Enable Windows Hypervisor Platform now? This needs admin permission and "
                    "takes effect after you restart your PC. Then open Setup again."
                ),
                enable=setup_wizard.enable_hypervisor_platform,
                enabling_message=(
                    "Enabling Hypervisor Platform (you may see a UAC prompt). "
                    "Restart your PC when it's done, then open Setup again (the Manager's Run Setup)."
                ),
                manual_hint='Try enabling "Windows Hypervisor Platform" yourself via "Turn Windows features on or off".',
            )
        elif "`kvm` group" in message:
            self._offer_fix(
                message, title="Add yourself to the kvm group?",
                confirm=(
                    "Add your account to the `kvm` group now? This asks for permission and "
                    "takes effect after you log out and back in. Then open Setup again."
                ),
                enable=setup_wizard.enable_kvm_access,
                enabling_message=(
                    "You may see a permission prompt. Log out and back in when it's done, "
                    "then open Setup again (the Manager's Run Setup)."
                ),
                manual_hint="Run in a terminal: sudo usermod -aG kvm $USER, then log out and back in.",
            )
        else:
            QMessageBox.critical(self, "Setup failed", f"{message}\n\nSee the log for details.")

    def _offer_fix(self, message: str, *, title: str, confirm: str, enable, enabling_message: str, manual_hint: str) -> None:
        answer = QMessageBox.question(self, title, f"{message}\n\n{confirm}")
        if answer != QMessageBox.StandardButton.Yes:
            QMessageBox.critical(self, "Setup failed", f"{message}\n\nSee the log for details.")
            return
        try:
            enable()
            QMessageBox.information(self, "Enabling...", enabling_message)
        except Exception as e:
            QMessageBox.critical(self, "Couldn't enable it automatically", f"{e}\n\n{manual_hint}")

    def _open_onboarding(self) -> None:
        """Runs right after a successful setup, unprompted. Launched with
        -m from the project root (not `cwd=BRIDGE_DIR` + a bare script
        path) so bridge.ui.onboarding_wizard's own package imports resolve
        regardless of where this process happened to start from, see
        boot_overlay_qt.py's docstring for the exact bug this avoids."""
        self._onboarding_process = subprocess.Popen(
            [sys.executable, "-m", "bridge.ui.onboarding_wizard"], cwd=str(BRIDGE_DIR.parent)
        )

    def _hide_and_wait_for_onboarding(self) -> None:
        self.hide()
        threading.Thread(target=self._wait_for_onboarding_then_exit, daemon=True).start()

    def _wait_for_onboarding_then_exit(self) -> None:
        self._onboarding_process.wait()
        QTimer.singleShot(0, self.close)


def main() -> None:
    from PySide6.QtWidgets import QApplication

    winapi.minimize_own_console()
    app = QApplication(sys.argv)
    apply_theme(app)
    window = SetupWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
