"""
GUI front-end for installer/setup_wizard.py. Replaces installer/setup_gui.py's
tkinter SetupApp during the Qt rewrite (see
C:\\Users\\Jaemin\\.claude\\plans\\robust-giggling-thompson.md).

Lets you pick your iiSU APK, then runs the whole first-time setup (SDK/AVD
bootstrap, patching, install) on a background thread while streaming its
progress into a log view. All the actual work lives in setup_wizard.py /
sdk_bootstrap.py / patch_iisu.py -- this is purely a front end for it.

Stays its own separate window rather than a page inside the Manager, since
a one-time install wizard is a different shape of problem than the
settings the Manager's sidebar covers afterward.
"""

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import setup_wizard

# PySide6 is this file's own GUI toolkit -- has to be confirmed installed
# before the imports below, which need it, not inside main().
setup_wizard.ensure_pyside6()

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QPlainTextEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

import winapi
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.gradient_divider import GradientDivider
from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
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

    # -- UI -------------------------------------------------

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
            "Patches your own copy of iiSU to hand off game launches to real PC\n"
            "emulators, and sets up a self-contained Android VM to run it in."
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

    # -- Log handling -------------------------------------------------

    def _append_log(self, text: str) -> None:
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)

    # -- Setup run -------------------------------------------------

    def _start_setup(self) -> None:
        if self.apk_path is None or self.running:
            return
        self.running = True
        self.start_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self._set_status("Running setup. This can take a long time on first run (several GB)...")
        self.progress.setRange(0, 0)

        self._setup_signals = run_in_background(self._run_setup_thread, self._on_setup_finished, apk_path=self.apk_path)

    def _run_setup_thread(self, apk_path: Path) -> Exception | None:
        old_stdout = sys.stdout
        sys.stdout = self._log_stream
        error: Exception | None = None
        try:
            setup_wizard.run_setup(apk_path, on_stage=self._on_stage)
        except Exception as e:  # noqa: BLE001 -- surfaced to the user below, not swallowed
            error = e
            print(f"\n[setup] FAILED: {e}\n")
            print(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
        return error

    def _on_stage(self, label: str, index: int, total: int) -> None:
        # Called from the worker thread -- emitting a Qt signal from here is
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
            if isinstance(error, setup_wizard.VirtualizationError):
                self._offer_hypervisor_fix(str(error))
            else:
                QMessageBox.critical(self, "Setup failed", f"{error}\n\nSee the log for details.")

    def _offer_hypervisor_fix(self, message: str) -> None:
        """VirtualizationError specifically (not every setup failure) means
        there's a concrete, one-click-away fix worth offering right in the
        dialog instead of leaving the person to go search for what
        "Windows Hypervisor Platform" even is."""
        if "Hypervisor Platform" not in message:
            QMessageBox.critical(self, "Setup failed", f"{message}\n\nSee the log for details.")
            return
        answer = QMessageBox.question(
            self,
            "Enable Windows Hypervisor Platform?",
            f"{message}\n\nEnable Windows Hypervisor Platform now? This asks Windows for admin "
            "permission and won't take effect until you restart your PC -- re-run Setup.bat "
            "after restarting.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                setup_wizard.enable_hypervisor_platform()
                QMessageBox.information(
                    self,
                    "Enabling...",
                    "Windows is enabling Hypervisor Platform now (you may see a UAC prompt). "
                    "Restart your PC once it's done, then re-run Setup.bat.",
                )
            except Exception as e:
                QMessageBox.critical(
                    self, "Couldn't enable it automatically",
                    f"{e}\n\nTry enabling \"Windows Hypervisor Platform\" yourself via \"Turn Windows features on or off\".",
                )
        else:
            QMessageBox.critical(self, "Setup failed", f"{message}\n\nSee the log for details.")

    def _open_onboarding(self) -> None:
        """Runs right after a successful setup, unprompted. Launched with
        -m from the project root (not `cwd=BRIDGE_DIR` + a bare script
        path) so bridge.ui.onboarding_wizard's own package imports resolve
        regardless of where this process happened to start from -- see
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
