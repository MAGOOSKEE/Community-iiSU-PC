"""Home page: status card, Start/Stop, quick actions, and the live log --
ports manager.py's _build_home_page and everything it drives (~lines
641-977 of the original). Threading collapses from thread+queue.Queue+
polling timer down to run_in_background() + a Signal-based log redirector,
since Qt already marshals cross-thread signal delivery on its own -- see
bridge/ui/workers/{task_runner,log_stream}.py's docstrings."""

import os
import sys
import traceback
from pathlib import Path

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import start_iisu_pc
import stop_iisu_pc
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.status_dot import StatusDot
from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
from bridge_config import CONFIG_PATH
from shared.qt_theme import Fonts, INPUT_BG, LOG_TEXT, TEXT_DIM

STATUS_POLL_INTERVAL_MS = 2000


class HomePage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        # `window` is the owning ManagerWindow: home needs its shared
        # config_data/configured state and a couple of cross-page actions
        # (rebuilding settings pages after Setup finishes), the same
        # coupling manager.py had by simply being one big class.
        self.window = window
        self.busy = False
        self._setup_process = None
        self._hidden_for_setup = False

        self.add_header("Community-iiSU-PC", "Android frontend, real PC emulators.")

        self.status_card = Card()
        status_layout = QGridLayout(self.status_card)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setHorizontalSpacing(10)
        status_layout.setVerticalSpacing(8)

        self.avd_dot = StatusDot()
        status_layout.addWidget(self.avd_dot, 0, 0)
        avd_label = QLabel("Android VM")
        avd_label.setFont(Fonts.heading())
        status_layout.addWidget(avd_label, 0, 1)
        self.avd_status_label = QLabel("checking...")
        self.avd_status_label.setStyleSheet(f"color: {TEXT_DIM};")
        status_layout.addWidget(self.avd_status_label, 0, 2)

        self.bridge_dot = StatusDot()
        status_layout.addWidget(self.bridge_dot, 1, 0)
        bridge_label = QLabel("Launch bridge")
        bridge_label.setFont(Fonts.heading())
        status_layout.addWidget(bridge_label, 1, 1)
        self.bridge_status_label = QLabel("checking...")
        self.bridge_status_label.setStyleSheet(f"color: {TEXT_DIM};")
        status_layout.addWidget(self.bridge_status_label, 1, 2)
        status_layout.setColumnStretch(2, 1)
        self.body_layout.addWidget(self.status_card)

        self.setup_intro_label = QLabel(
            "Community-iiSU-PC hasn't been set up yet. Setup installs a self-contained Android VM "
            "and\npatches your copy of iiSU to hand off game launches to real PC emulators."
        )
        self.setup_intro_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.setup_intro_label)

        self.button_row = QWidget()
        button_layout = QHBoxLayout(self.button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(10)

        self.primary_button = QPushButton("Run Setup")
        self.primary_button.setObjectName("accent")
        button_layout.addWidget(self.primary_button)

        self.roms_folder_button = QPushButton("ROMs Folder")
        self.roms_folder_button.setObjectName("ghost")
        self.roms_folder_button.clicked.connect(self._open_roms_folder)
        button_layout.addWidget(self.roms_folder_button)

        self.logs_button = QPushButton("Logs")
        self.logs_button.setObjectName("ghost")
        self.logs_button.clicked.connect(self._open_logs)
        button_layout.addWidget(self.logs_button)

        self.shortcut_button = QPushButton("Recreate Shortcut")
        self.shortcut_button.setObjectName("ghost")
        self.shortcut_button.clicked.connect(self._recreate_desktop_shortcut)
        button_layout.addWidget(self.shortcut_button)

        self.clear_resume_button = QPushButton("Clear Resume State")
        self.clear_resume_button.setObjectName("ghost")
        self.clear_resume_button.clicked.connect(self._clear_resume_state)
        button_layout.addWidget(self.clear_resume_button)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        button_layout.addWidget(self.progress, 1)

        self.body_layout.addWidget(self.button_row)

        self.resume_reason_label = QLabel("")
        self.resume_reason_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.resume_reason_label)

        self.stage_label = QLabel("")
        self.stage_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.stage_label)

        log_card = Card()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(10, 10, 10, 10)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        self.log_text.setStyleSheet(f"background-color: {INPUT_BG}; color: {LOG_TEXT}; border: none;")
        log_layout.addWidget(self.log_text)
        self.body_layout.addWidget(log_card, 1)

        self._log_redirector = LogStreamRedirector()
        self._log_redirector.text_written.connect(self._append_log)
        self._guarded_signals = None
        self._shortcut_signals = None
        self._status_signals = None

        self.primary_button.clicked.connect(self._on_primary_click)
        self.refresh_home_state()

    # -- Home state -------------------------------------------------

    def refresh_home_state(self) -> None:
        self.setup_intro_label.setVisible(not self.window.configured)
        self.status_card.setVisible(self.window.configured)
        self._refresh_primary_button()
        self._refresh_resume_reason()

    def refresh_resume_reason(self) -> None:
        self._refresh_resume_reason()

    def _refresh_resume_reason(self) -> None:
        if not self.window.configured:
            self.resume_reason_label.setText("")
            return
        new_parts = start_iisu_pc.compute_boot_fingerprint_parts(self.window.config_data)
        old_parts = start_iisu_pc.load_saved_boot_fingerprint_parts()
        reasons = start_iisu_pc.describe_boot_fingerprint_diff(old_parts, new_parts)
        if reasons:
            self.resume_reason_label.setText(f"Next start: cold boot ({', '.join(reasons)})")
        else:
            self.resume_reason_label.setText("Next start: quick resume (nothing relevant has changed)")

    def _clear_resume_state(self) -> None:
        reply = QMessageBox.question(
            self,
            "Clear Resume State",
            "This forces the next start to do a full cold boot instead of a quick resume.\n\n"
            "Use this if the Android VM seems stuck in a bad state after resuming. It doesn't "
            "affect your settings, ROM library, or the VM itself.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        start_iisu_pc.clear_boot_fingerprint()
        self._refresh_resume_reason()
        QMessageBox.information(self, "Clear Resume State", "Done. The next start will be a full cold boot.")

    def _refresh_primary_button(self) -> None:
        if not self.window.configured:
            running = self._setup_process is not None and self._setup_process.poll() is None
            self.primary_button.setText("Setup running..." if running else "Run Setup")
            try:
                self.primary_button.clicked.disconnect()
            except TypeError:
                pass
            self.primary_button.clicked.connect(self._on_primary_click)
            self.primary_button.setEnabled(not running)
        elif self.window.last_bridge_up or self.window.last_avd_up:
            self.primary_button.setText("Stop")
            self.primary_button.setEnabled(not self.busy)
        else:
            self.primary_button.setText("Open")
            self.primary_button.setEnabled(not self.busy)

    def _on_primary_click(self) -> None:
        if not self.window.configured:
            self._start_setup_flow()
        elif self.window.last_bridge_up or self.window.last_avd_up:
            self._stop()
        else:
            self._start()

    def _start_setup_flow(self) -> None:
        import subprocess

        if self._setup_process is not None and self._setup_process.poll() is None:
            return
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        self._setup_process = subprocess.Popen([sys.executable, "-m", "bridge.ui.setup_app"], cwd=str(project_root))
        self._refresh_primary_button()
        # Hidden, not closed, so the Manager reappears exactly where it was
        # once Setup (and the onboarding wizard it hands off to) exits (see
        # _apply_status) -- an idle Manager window sitting behind Setup
        # serves no purpose.
        self.window.hide()
        self._hidden_for_setup = True

    def _start(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.stage_label.setText("Starting...")
        self._append_log("\n--- Start ---\n")
        self._guarded_signals = run_in_background(self._run_guarded, self._on_guarded_done, self._on_guarded_error, start_iisu_pc.main)

    def _stop(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.stage_label.setText("Stopping...")
        self._append_log("\n--- Stop ---\n")
        self._guarded_signals = run_in_background(self._run_guarded, self._on_guarded_done, self._on_guarded_error, stop_iisu_pc.main)

    def _run_guarded(self, func) -> None:
        old_stdout = sys.stdout
        sys.stdout = self._log_redirector
        try:
            func()
        except SystemExit as e:
            if e.code not in (0, None):
                print(f"\n[manager] exited with code {e.code}\n")
        except Exception:
            print(f"\n[manager] error:\n{traceback.format_exc()}")
        finally:
            sys.stdout = old_stdout

    def _on_guarded_done(self, _result) -> None:
        self._set_busy(False)
        self._refresh_resume_reason()

    def _on_guarded_error(self, message: str) -> None:
        self._set_busy(False)
        self._append_log(f"\n[manager] error:\n{message}\n")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.progress.setVisible(busy)
        self._refresh_primary_button()

    def _append_log(self, text: str) -> None:
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.ensureCursorVisible()
        self._update_stage_label(text)

    def _update_stage_label(self, text: str) -> None:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("[start] ") or line.startswith("[stop] "):
                self.stage_label.setText(line.split("] ", 1)[1])

    def _open_roms_folder(self) -> None:
        if not self.window.configured:
            return
        roms_dir = Path(self.window.config_data.get("roms_dir", ""))
        if not roms_dir.is_dir():
            QMessageBox.critical(self, "Can't open ROMs folder", f"{roms_dir} doesn't exist yet. Set it up in ROM Directory first.")
            return
        os.startfile(roms_dir)

    def _open_logs(self) -> None:
        bridge_dir = Path(__file__).resolve().parent.parent.parent.parent / "bridge"
        os.startfile(bridge_dir)

    def _recreate_desktop_shortcut(self) -> None:
        self.shortcut_button.setEnabled(False)
        self.shortcut_button.setText("Creating...")
        self._shortcut_signals = run_in_background(self._recreate_shortcut_worker, self._on_shortcut_recreated, self._on_shortcut_error)

    def _recreate_shortcut_worker(self):
        import create_shortcut

        return create_shortcut.create_desktop_shortcut()

    def _on_shortcut_recreated(self, path) -> None:
        self.shortcut_button.setEnabled(True)
        self.shortcut_button.setText("Recreate Shortcut")
        QMessageBox.information(self, "Shortcut", f"Desktop shortcut created:\n{path}")

    def _on_shortcut_error(self, message: str) -> None:
        self.shortcut_button.setEnabled(True)
        self.shortcut_button.setText("Recreate Shortcut")
        QMessageBox.critical(self, "Shortcut", f"Couldn't create the desktop shortcut:\n{message}")

    # -- Status polling (called by ManagerWindow's shared timer) -------------------------------------------------

    def poll_status(self) -> None:
        self._status_signals = run_in_background(self._check_status, self._apply_status)

    def _check_status(self):
        avd_up = bridge_up = None
        if self.window.configured:
            try:
                config = start_iisu_pc.load_config()
                avd_up = start_iisu_pc.is_avd_running(config["avd_name"])
                bridge_up = start_iisu_pc.is_port_open(config["bridge_port"])
            except Exception:
                pass
        now_configured = CONFIG_PATH.is_file()
        return (avd_up, bridge_up, now_configured)

    def _apply_status(self, result) -> None:
        avd_up, bridge_up, now_configured = result
        self.window.last_avd_up = avd_up
        self.window.last_bridge_up = bridge_up

        setup_just_exited = (
            self._hidden_for_setup
            and self._setup_process is not None
            and self._setup_process.poll() is not None
        )

        if now_configured != self.window.configured or setup_just_exited:
            self.window.reload_config()
            self.window.refresh_nav_enabled()
            self.refresh_home_state()
            self.window.rebuild_settings_pages()

        if setup_just_exited:
            self._hidden_for_setup = False
            self.window.show_page("home")
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()

        if not self.busy:
            if avd_up is None:
                self.avd_dot.set_state("unknown")
                self.avd_status_label.setText("unknown")
            else:
                self.avd_dot.set_state("up" if avd_up else "down")
                self.avd_status_label.setText("running" if avd_up else "stopped")
            if bridge_up is None:
                self.bridge_dot.set_state("unknown")
                self.bridge_status_label.setText("unknown")
            else:
                self.bridge_dot.set_state("up" if bridge_up else "down")
                self.bridge_status_label.setText("running" if bridge_up else "stopped")

        self._refresh_primary_button()
        self.window.refresh_save_lock(avd_up, bridge_up)

    def confirm_close(self) -> bool:
        """True if it's OK to close the main window -- warns first when the
        VM/bridge are still running in the background, matching manager.py's
        _on_close."""
        if self.window.last_avd_up or self.window.last_bridge_up:
            reply = QMessageBox.question(
                self.window,
                "Community-iiSU-PC is still running",
                "The Android VM and/or launch bridge are still running in the background.\n\n"
                "Closing this window will NOT stop them -- use Stop first if you want to shut "
                "everything down.\n\nClose this window anyway?",
            )
            return reply == QMessageBox.StandardButton.Yes
        return True
