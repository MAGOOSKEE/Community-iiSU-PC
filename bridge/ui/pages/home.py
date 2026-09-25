"""Home page: status card, Start/Stop, quick actions, and the live log,
ports manager.py's _build_home_page and everything it drives (~lines
641-977 of the original). Threading collapses from thread+queue.Queue+
polling timer down to run_in_background() + a Signal-based log redirector,
since Qt already marshals cross-thread signal delivery on its own, see
bridge/ui/workers/{task_runner,log_stream}.py's docstrings."""

import os
import sys
import traceback
from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
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
from bridge.ui.widgets.gradient_background import GradientBlobBackground
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

        # Painted behind everything else on this page -- created before any
        # body_layout.addWidget() call below so it sits at the bottom of the
        # sibling stacking order, and never added to body_layout itself
        # since it's positioned manually (see resizeEvent), not managed by
        # the page's own layout. Same technique bridge/ui/boot_overlay_app.py
        # uses for its own background.
        self._background = GradientBlobBackground(self)
        self._background.lower()

        self.add_header("Community-iiSU-PC", "Android frontend, real PC emulators.")

        self.body_layout.addStretch(1)

        # Everything below is the one thing this page is actually for:
        # start/stop iiSU. Status, resume-state, and log detail all used to
        # sit here permanently as their own buttons/cards; now they're a
        # compact status line, an Advanced Launch Options menu, and a
        # collapsed-by-default log drawer, so the page reads as one big
        # button rather than a control panel.
        center_col = QWidget()
        center_layout = QVBoxLayout(center_col)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.status_row = QWidget()
        status_row_layout = QHBoxLayout(self.status_row)
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_row_layout.setSpacing(18)
        status_row_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        avd_col = QHBoxLayout()
        avd_col.setSpacing(6)
        self.avd_dot = StatusDot()
        avd_col.addWidget(self.avd_dot)
        avd_label = QLabel("Android VM")
        avd_label.setStyleSheet(f"color: {TEXT_DIM};")
        avd_col.addWidget(avd_label)
        self.avd_status_label = QLabel("checking...")
        self.avd_status_label.setStyleSheet(f"color: {TEXT_DIM};")
        avd_col.addWidget(self.avd_status_label)
        status_row_layout.addLayout(avd_col)

        bridge_col = QHBoxLayout()
        bridge_col.setSpacing(6)
        self.bridge_dot = StatusDot()
        bridge_col.addWidget(self.bridge_dot)
        bridge_label = QLabel("Launch bridge")
        bridge_label.setStyleSheet(f"color: {TEXT_DIM};")
        bridge_col.addWidget(bridge_label)
        self.bridge_status_label = QLabel("checking...")
        self.bridge_status_label.setStyleSheet(f"color: {TEXT_DIM};")
        bridge_col.addWidget(self.bridge_status_label)
        status_row_layout.addLayout(bridge_col)

        center_layout.addWidget(self.status_row)

        self.setup_intro_label = QLabel(
            "Community-iiSU-PC hasn't been set up yet. Setup installs a self-contained Android VM "
            "and patches your copy of iiSU to hand off game launches to real PC emulators."
        )
        self.setup_intro_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setup_intro_label.setWordWrap(True)
        self.setup_intro_label.setMaximumWidth(520)
        self.setup_intro_label.setStyleSheet(f"color: {TEXT_DIM};")
        center_layout.addWidget(self.setup_intro_label)

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(6)

        self.primary_button = QPushButton("Start iiSU")
        self.primary_button.setObjectName("accent")
        primary_font = QFont(self.primary_button.font())
        primary_font.setPointSize(primary_font.pointSize() + 4)
        self.primary_button.setFont(primary_font)
        self.primary_button.setMinimumSize(220, 56)
        button_layout.addWidget(self.primary_button)

        self.advanced_button = QPushButton("▾")  # small dropdown caret
        self.advanced_button.setObjectName("ghost")
        self.advanced_button.setFixedSize(32, 56)
        self.advanced_button.setToolTip("Advanced launch options")
        self.advanced_button.clicked.connect(self._show_advanced_menu)
        button_layout.addWidget(self.advanced_button)

        center_layout.addWidget(button_row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setFixedWidth(220)
        self.progress.hide()
        center_layout.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)

        self.resume_reason_label = QLabel("")
        self.resume_reason_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.resume_reason_label.setStyleSheet(f"color: {TEXT_DIM};")
        center_layout.addWidget(self.resume_reason_label)

        self.stage_label = QLabel("")
        self.stage_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.stage_label.setStyleSheet(f"color: {TEXT_DIM};")
        center_layout.addWidget(self.stage_label)

        self.body_layout.addWidget(center_col, 0, Qt.AlignmentFlag.AlignHCenter)
        self.body_layout.addStretch(1)

        # Collapsed by default -- the log drawer, not a permanently visible
        # console. _run_guarded's redirector keeps appending to it whether
        # it's shown or not, so opening it mid-run shows history, not just
        # new output.
        self.logs_toggle_button = QPushButton("Show Logs ▾")
        self.logs_toggle_button.setObjectName("ghost")
        self.logs_toggle_button.clicked.connect(self._toggle_logs_drawer)
        self.body_layout.addWidget(self.logs_toggle_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.log_drawer = Card()
        log_layout = QVBoxLayout(self.log_drawer)
        log_layout.setContentsMargins(10, 10, 10, 10)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        self.log_text.setStyleSheet(f"background-color: {INPUT_BG}; color: {LOG_TEXT}; border: none;")
        self.log_text.setFixedHeight(220)
        log_layout.addWidget(self.log_text)
        self.log_drawer.setVisible(False)
        self.body_layout.addWidget(self.log_drawer)

        self._log_redirector = LogStreamRedirector()
        self._log_redirector.text_written.connect(self._append_log)
        self._guarded_signals = None
        self._shortcut_signals = None
        self._status_signals = None

        self.primary_button.clicked.connect(self._on_primary_click)
        self.refresh_home_state()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._background.setGeometry(self.rect())

    def _show_advanced_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("ROMs Folder", self._open_roms_folder)
        menu.addAction("Open Logs Folder", self._open_logs)
        menu.addAction("Recreate Shortcut", self._recreate_desktop_shortcut)
        menu.addSeparator()
        menu.addAction("Clear Resume State", self._clear_resume_state)
        menu.exec(self.advanced_button.mapToGlobal(self.advanced_button.rect().bottomRight()))

    def _toggle_logs_drawer(self) -> None:
        showing = not self.log_drawer.isVisible()
        self.log_drawer.setVisible(showing)
        self.logs_toggle_button.setText("Hide Logs ▴" if showing else "Show Logs ▾")

    # == Home state ==

    def refresh_home_state(self) -> None:
        self.setup_intro_label.setVisible(not self.window.configured)
        self.status_row.setVisible(self.window.configured)
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
            self.primary_button.setText("Start iiSU")
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
        # _apply_status), an idle Manager window sitting behind Setup
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
        # A one-shot menu action now rather than its own always-visible
        # button, so the advanced-options button itself is the "busy"
        # indicator that stops a second click starting a second run.
        self.advanced_button.setEnabled(False)
        self._shortcut_signals = run_in_background(self._recreate_shortcut_worker, self._on_shortcut_recreated, self._on_shortcut_error)

    def _recreate_shortcut_worker(self):
        import create_shortcut

        return create_shortcut.create_desktop_shortcut()

    def _on_shortcut_recreated(self, path) -> None:
        self.advanced_button.setEnabled(True)
        QMessageBox.information(self, "Shortcut", f"Desktop shortcut created:\n{path}")

    def _on_shortcut_error(self, message: str) -> None:
        self.advanced_button.setEnabled(True)
        QMessageBox.critical(self, "Shortcut", f"Couldn't create the desktop shortcut:\n{message}")

    # == Status polling (called by ManagerWindow's shared timer) ==

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
        """True if it's OK to close the main window, warns first when the
        VM/bridge are still running in the background, matching manager.py's
        _on_close."""
        if self.window.last_avd_up or self.window.last_bridge_up:
            reply = QMessageBox.question(
                self.window,
                "Community-iiSU-PC is still running",
                "The Android VM and/or launch bridge are still running in the background.\n\n"
                "Closing this window will NOT stop them, use Stop first if you want to shut "
                "everything down.\n\nClose this window anyway?",
            )
            return reply == QMessageBox.StandardButton.Yes
        return True
