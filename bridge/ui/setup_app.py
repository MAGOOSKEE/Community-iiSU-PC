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

import setup_wizard

# PySide6 is this file's own GUI toolkit, has to be confirmed installed
# before the imports below, which need it, not inside main().
setup_wizard.ensure_pyside6()

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QPlainTextEdit, QPushButton,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

import winapi
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.gradient_divider import GradientDivider
from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
from shared import vm_storage
from shared.qt_theme import Fonts, GREEN, RED, SPACING_LG, SPACING_MD, SPACING_SM, apply_theme

BRIDGE_DIR = setup_wizard.BRIDGE_DIR


class SetupWindow(QMainWindow):
    stage_changed = Signal(str, int, int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Community-iiSU-PC Setup")
        self.resize(720, 600)
        self.setMinimumSize(620, 480)

        self.apk_path: Path | None = None
        self.running = False
        self._onboarding_process: subprocess.Popen | None = None

        self._log_stream = LogStreamRedirector()
        self._log_stream.text_written.connect(self._append_log)
        self.stage_changed.connect(self._apply_stage)

        self._build_ui()
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
        root.addWidget(GradientDivider())
        root.addSpacing(10)

        apk_card = Card()
        apk_layout = QVBoxLayout(apk_card)
        apk_layout.setContentsMargins(SPACING_MD, SPACING_MD - 2, SPACING_MD, SPACING_MD - 2)
        apk_heading = QLabel("iiSU APK")
        apk_heading.setFont(Fonts.heading())
        apk_layout.addWidget(apk_heading)

        apk_row = QHBoxLayout()
        self.apk_label = QLabel("No APK selected.")
        self.apk_label.setProperty("role", "dim")
        apk_row.addWidget(self.apk_label, stretch=1)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self._browse_apk)
        apk_row.addWidget(self.browse_button)
        apk_layout.addLayout(apk_row)
        root.addWidget(apk_card)

        vm_card = Card()
        vm_layout = QVBoxLayout(vm_card)
        vm_layout.setContentsMargins(SPACING_MD, SPACING_MD - 2, SPACING_MD, SPACING_MD - 2)
        vm_heading = QLabel("Android VM")
        vm_heading.setFont(Fonts.heading())
        vm_layout.addWidget(vm_heading)

        storage_row = QHBoxLayout()
        storage_label = QLabel("Internal storage")
        storage_row.addWidget(storage_label)
        self.storage_spin = QSpinBox()
        self.storage_spin.setRange(vm_storage.MIN_DATA_PARTITION_GB, vm_storage.MAX_DATA_PARTITION_GB)
        self.storage_spin.setValue(vm_storage.DEFAULT_DATA_PARTITION_GB)
        self.storage_spin.setSuffix(" GB")
        self.storage_spin.setToolTip(
            "The most the VM can hold. The file on your drive only grows as the VM fills, "
            "so a bigger number costs nothing until used. Changeable later in Settings, "
            "but that recreates the VM."
        )
        storage_row.addWidget(self.storage_spin)
        storage_hint = QLabel("A maximum, not a reservation.")
        storage_hint.setProperty("role", "dim")
        storage_row.addWidget(storage_hint, stretch=1)
        vm_layout.addLayout(storage_row)

        self.lean_check = QCheckBox("Lean VM: remove apps iiSU doesn't need (keeps Files, Chrome, Play Store)")
        self.lean_check.setChecked(True)
        self.lean_check.setToolTip(
            "Removes Maps, Photos, YouTube, Gmail and other bundled apps, installs LeanKeyboard, "
            "and checks the VM still boots. Uses less memory and disk. Untick for a stock VM."
        )
        vm_layout.addWidget(self.lean_check)
        root.addWidget(vm_card)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("Start Setup")
        self.start_button.setObjectName("accent")
        self.start_button.setEnabled(False)
        self.start_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.start_button.clicked.connect(self._start_setup)
        action_row.addWidget(self.start_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        action_row.addWidget(self.progress, stretch=1)
        root.addLayout(action_row)

        log_card = Card()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(SPACING_SM + 2, SPACING_SM + 2, SPACING_SM + 2, SPACING_SM + 2)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        log_layout.addWidget(self.log_text)
        root.addWidget(log_card, stretch=1)

        self.status_label = QLabel("Ready.")
        self.status_label.setProperty("role", "dim")
        root.addWidget(self.status_label)

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
        if not self.running:
            self.start_button.setEnabled(True)

    # == Log handling ==

    def _append_log(self, text: str) -> None:
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)

    # == Setup run ==

    def _start_setup(self) -> None:
        if self.apk_path is None or self.running:
            return
        self.running = True
        self.start_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.storage_spin.setEnabled(False)
        self.lean_check.setEnabled(False)
        self._set_status("Running setup. The first run can take a while (several GB)...")
        self.progress.setRange(0, 0)

        self._setup_signals = run_in_background(
            self._run_setup_thread, self._on_setup_finished,
            apk_path=self.apk_path, storage_gb=self.storage_spin.value(), lean=self.lean_check.isChecked(),
        )

    def _run_setup_thread(self, apk_path: Path, storage_gb: int, lean: bool) -> Exception | None:
        old_stdout = sys.stdout
        sys.stdout = self._log_stream
        error: Exception | None = None
        try:
            with setup_wizard.session_log():
                try:
                    setup_wizard.run_setup(apk_path, on_stage=self._on_stage, storage_gb=storage_gb, lean=lean)
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
        self._set_status(f"Step {index}/{total}: {label}...")

    def _on_setup_finished(self, error: Exception | None) -> None:
        self.running = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if error is None else 0)
        self.browse_button.setEnabled(True)
        self.storage_spin.setEnabled(True)
        self.lean_check.setEnabled(True)
        self.start_button.setEnabled(True)

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
        self.progress.setRange(0, 0)
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
        self.progress.setRange(0, 100)
        self.browse_button.setEnabled(True)
        self.start_button.setEnabled(True)
        if error is not None:
            self.progress.setValue(0)
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
                    "takes effect after you restart your PC. Then re-run Setup.bat."
                ),
                enable=setup_wizard.enable_hypervisor_platform,
                enabling_message=(
                    "Enabling Hypervisor Platform (you may see a UAC prompt). "
                    "Restart your PC when it's done, then re-run Setup.bat."
                ),
                manual_hint='Try enabling "Windows Hypervisor Platform" yourself via "Turn Windows features on or off".',
            )
        elif "`kvm` group" in message:
            self._offer_fix(
                message, title="Add yourself to the kvm group?",
                confirm=(
                    "Add your account to the `kvm` group now? This asks for permission and "
                    "takes effect after you log out and back in. Then re-run Setup.sh."
                ),
                enable=setup_wizard.enable_kvm_access,
                enabling_message=(
                    "You may see a permission prompt. Log out and back in when it's done, "
                    "then re-run Setup.sh."
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
