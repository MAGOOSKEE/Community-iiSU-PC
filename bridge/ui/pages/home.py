"""Home page: the mark and name, Start/Stop, status, quick actions, and the live log.
Threading uses run_in_background() + a Signal-based log redirector, since
Qt already marshals cross-thread signal delivery on its own, see
bridge/ui/workers/{task_runner,log_stream}.py's docstrings."""

import os
import random
import sys
import traceback
from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
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
from bridge.ui.widgets.brand_mark import BrandMark
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.elided_label import ElidedLabel
from bridge.ui.widgets.gradient_background import GradientBlobBackground
from bridge.ui.widgets.gradient_divider import GradientDivider
from bridge.ui.widgets.status_dot import StatusDot
from shared.platform_compat import open_uri
from shared.vm_backend import supports_headless_boot
from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
import bridge_config
import launch_problems
from bridge_config import CONFIG_PATH
from shared.qt_theme import Fonts, INPUT_BG, LOG_TEXT, RED, TEXT_DIM


STATUS_POLL_INTERVAL_MS = 2000
HERO_LOGO_HEIGHT = 72

# The line under the name. A different one each time the Manager opens.
TAGLINES = (
    "It's game time!",
    "Android frontend, real PC emulators.",
    "What are we playing today?",
    "Ready when you are.",
    "Pick up where you left off.",
    "Every console, one library.",
    "Controllers at the ready.",
    "Press Start to play.",
    "One more game couldn't hurt.",
    "Your library missed you.",
    "Insert coin.",
    "Player one, ready.",
    "Free play, no coins needed.",
    "Your arcade, at home.",
    "All your games, one place.",
    "Your whole collection awaits.",
    "Every console, one launcher.",
    "Pick a game, any game.",
    "Your games, your way.",
    "Welcome back, player.",
    "Good to see you again.",
    "Let's get playing.",
    "Time to play.",
    "Ready to play?",
    "Game on.",
    "Let's go.",
    "Ready, set, play.",
    "Your move.",
    "Load up and play.",
)

STEAM_SHORTCUT_NOTE = (
    "Adds Community-iiSU-PC as a non-Steam game, for Steam's library, Big Picture or Game Mode.\n"
    "Close Steam first, it rewrites this list on exit. The shortcut starts the VM and bridge, then\n"
    "ends, so Steam shows it stopped while iiSU keeps running."
)
LAUNCH_OPTIONS_TOOLTIP = "Launch options"


class HomePage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        # `window` is the owning ManagerWindow: home needs its shared
        # config_data/configured state and a couple of cross-page actions
        # (rebuilding settings pages after Setup finishes).
        self.window = window
        self.busy = False
        self._setup_process = None
        self._hidden_for_setup = False

        # Painted behind everything else on this page, created before any
        # body_layout.addWidget() call below so it sits at the bottom of the
        # sibling stacking order, and never added to body_layout itself
        # since it's positioned manually (see resizeEvent), not managed by
        # the page's own layout. Same technique bridge/ui/boot_overlay_app.py
        # uses for its own background.
        self._background = GradientBlobBackground(self)
        self._background.lower()

        self.body_layout.addStretch(2)

        # One centred column: who this is (mark, name, tagline), the one
        # thing the page is for (start/stop iiSU), then what is running.
        # Everything else stays out of the way in the menu next to the
        # button: what the next start will be is its tooltip, and the log
        # drawer is a tick box in it, so the page reads as one big button
        # rather than a control panel.
        center_col = QWidget()
        center_layout = QVBoxLayout(center_col)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # The iiPC mark, its "ii" drifting through the brand gradient.
        self.brand_mark = BrandMark(HERO_LOGO_HEIGHT, animated=True)
        if self.brand_mark.is_available():
            center_layout.addWidget(self.brand_mark, 0, Qt.AlignmentFlag.AlignHCenter)
            center_layout.addSpacing(20)

        name_label = QLabel("Community-iiSU-PC")
        name_label.setFont(Fonts.brand(28))
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        center_layout.addWidget(name_label)
        center_layout.addSpacing(4)

        self.tagline_label = QLabel(random.choice(TAGLINES))
        self.tagline_label.setFont(Fonts.ui(11))
        self.tagline_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.tagline_label.setStyleSheet(f"color: {TEXT_DIM};")
        center_layout.addWidget(self.tagline_label)
        center_layout.addSpacing(16)

        divider = GradientDivider()
        divider.setFixedWidth(200)
        center_layout.addWidget(divider, 0, Qt.AlignmentFlag.AlignHCenter)
        center_layout.addSpacing(28)

        self.setup_intro_label = QLabel(
            "Not set up yet. Setup installs a self-contained Android VM and patches your copy "
            "of iiSU to hand game launches to PC emulators."
        )
        self.setup_intro_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setup_intro_label.setWordWrap(True)
        self.setup_intro_label.setMaximumWidth(520)
        self.setup_intro_label.setContentsMargins(0, 0, 0, 16)
        self.setup_intro_label.setStyleSheet(f"color: {TEXT_DIM};")
        center_layout.addWidget(self.setup_intro_label)

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)

        # The brand gradient while it starts something, a quiet outline
        # while it stops it (see #HomePrimary in shared/qt_theme.py).
        self.primary_button = QPushButton("Start iiSU")
        self.primary_button.setObjectName("HomePrimary")
        self.primary_button.setFont(Fonts.ui(14, semibold=True))
        self.primary_button.setMinimumSize(280, 58)
        self.primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # A soft glow in the gradient's own blue, only while it is the
        # bright Start button (see _restyle_primary_button).
        self._primary_glow = QGraphicsDropShadowEffect(self.primary_button)
        self._primary_glow.setBlurRadius(42)
        self._primary_glow.setOffset(0, 6)
        self._primary_glow.setColor(QColor(110, 140, 255, 120))
        self.primary_button.setGraphicsEffect(self._primary_glow)
        button_layout.addWidget(self.primary_button)

        self.advanced_button = QPushButton("▾")  # small dropdown caret
        self.advanced_button.setObjectName("HomeCaret")
        self.advanced_button.setFixedSize(44, 58)
        # Says what the next start will be (see _refresh_resume_reason).
        self.advanced_button.setToolTip(LAUNCH_OPTIONS_TOOLTIP)
        self.advanced_button.setAccessibleName("Launch options")
        self.advanced_button.clicked.connect(self._show_advanced_menu)
        button_layout.addWidget(self.advanced_button)

        center_layout.addWidget(button_row, 0, Qt.AlignmentFlag.AlignHCenter)
        center_layout.addSpacing(10)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setFixedWidth(332)
        self.progress.hide()
        center_layout.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)
        center_layout.addSpacing(10)

        self.status_row = QWidget()
        status_row_layout = QHBoxLayout(self.status_row)
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_row_layout.setSpacing(10)
        status_row_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        avd_chip, self.avd_dot, self.avd_status_label = self._status_chip("Android VM")
        avd_chip.setToolTip("The Android system iiSU runs in, on this PC.")
        status_row_layout.addWidget(avd_chip)
        bridge_chip, self.bridge_dot, self.bridge_status_label = self._status_chip("Launch bridge")
        bridge_chip.setToolTip("The part on your PC that opens a game in its PC emulator when you pick it in iiSU.")

        status_row_layout.addWidget(bridge_chip)
        center_layout.addWidget(self.status_row, 0, Qt.AlignmentFlag.AlignHCenter)
        center_layout.addSpacing(14)

        # One line, cut short with the full text in the tooltip: a wrapped line
        # here grew taller than the layout allowed for and drew over the button.
        self.stage_label = ElidedLabel(width=520)
        self.stage_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.stage_label.setStyleSheet(f"color: {TEXT_DIM};")
        center_layout.addWidget(self.stage_label)
        center_layout.addSpacing(14)

        # The last game that failed to open, kept after the on-screen card
        # has gone (see bridge/launch_problems.py), until it's dismissed.
        self.problem_card = QFrame()
        self.problem_card.setObjectName("ProblemCard")
        self.problem_card.setFixedWidth(540)
        problem_layout = QVBoxLayout(self.problem_card)
        problem_layout.setContentsMargins(16, 12, 16, 12)
        problem_layout.setSpacing(6)
        self.problem_title = QLabel("")
        self.problem_title.setStyleSheet(f"color: {RED}; font-weight: 600;")
        problem_layout.addWidget(self.problem_title)
        self.problem_text = QLabel("")
        self.problem_text.setWordWrap(True)
        self.problem_text.setFixedWidth(540 - 32)

        problem_layout.addWidget(self.problem_text)
        problem_buttons = QHBoxLayout()
        problem_buttons.addStretch(1)
        open_history = QPushButton("Open Launch Log")
        open_history.setObjectName("ghost")
        open_history.clicked.connect(lambda: open_uri(str(launch_problems.LAST_PROBLEM_PATH.parent / "launch_history.log")))
        problem_buttons.addWidget(open_history)
        dismiss = QPushButton("Dismiss")
        dismiss.setObjectName("ghost")
        dismiss.clicked.connect(self._dismiss_problem)
        problem_buttons.addWidget(dismiss)
        problem_layout.addLayout(problem_buttons)
        self.problem_card.hide()
        center_layout.addWidget(self.problem_card, 0, Qt.AlignmentFlag.AlignHCenter)

        self.body_layout.addWidget(center_col, 0, Qt.AlignmentFlag.AlignHCenter)
        self.body_layout.addStretch(3)

        # Hidden by default: the log drawer, not a permanently visible
        # console, switched on with the Show Logs tick box in the menu next
        # to the button. _run_guarded's redirector keeps appending to it
        # whether it's shown or not, so opening it mid-run shows history,
        # not just new output.
        self.log_drawer = Card()
        log_layout = QVBoxLayout(self.log_drawer)
        log_layout.setContentsMargins(10, 10, 10, 10)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        self.log_text.setStyleSheet(f"background-color: {INPUT_BG}; color: {LOG_TEXT}; border: none;")
        self.log_text.setFixedHeight(220)
        log_layout.addWidget(self.log_text)
        self._logs_shown = bool((getattr(self.window, "config_data", None) or {}).get("home_show_logs", False))
        self.log_drawer.setVisible(self._logs_shown)
        self.body_layout.addWidget(self.log_drawer)

        self._log_redirector = LogStreamRedirector()
        self._log_redirector.text_written.connect(self._append_log)
        self._guarded_signals = None
        self._shortcut_signals = None
        self._status_signals = None

        self.primary_button.clicked.connect(self._on_primary_click)
        self.refresh_home_state()

    def _status_chip(self, name: str) -> tuple[QFrame, StatusDot, QLabel]:
        """A rounded pill: a status dot, what it is, and its state."""
        chip = QFrame()
        chip.setObjectName("StatusChip")
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(12, 6, 14, 6)
        layout.setSpacing(8)
        dot = StatusDot(size=8)
        layout.addWidget(dot)
        layout.addWidget(QLabel(name))
        status_label = QLabel("checking...")
        status_label.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(status_label)
        return chip, dot, status_label

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._background.setGeometry(self.rect())

    def _show_advanced_menu(self) -> None:
        menu = self._build_advanced_menu()
        menu.exec(self.advanced_button.mapToGlobal(self.advanced_button.rect().bottomRight()))

    def _build_advanced_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        boot_headless = menu.addAction("Start in the Background (no window)", self._start_headless)
        boot_headless.setToolTip("Starts Android without its window or the launch bridge, for running it unattended.")
        # Only meaningful when nothing is running yet.
        boot_headless.setEnabled(
            self.window.configured and not self.busy and not (self.window.last_bridge_up or self.window.last_avd_up)
            and supports_headless_boot(self.window.config_data)
        )
        menu.addSeparator()
        sync_now = menu.addAction("Sync Library Now", self._sync_library_now)
        sync_now.setToolTip("Brings new or removed games into iiSU without restarting, then has iiSU rescan.")
        # Needs Android up and nothing else running from here.
        sync_now.setEnabled(bool(self.window.last_avd_up) and not self.busy)
        menu.addAction("ROMs Folder", self._open_roms_folder)
        menu.addAction("Open Logs Folder", self._open_logs)
        menu.addAction("Recreate Shortcut", self._recreate_desktop_shortcut)
        menu.addSeparator()
        # Only once there is an install for the Steam shortcut to start.
        add_to_steam = menu.addAction("Add to Steam", self._add_to_steam)
        add_to_steam.setToolTip(STEAM_SHORTCUT_NOTE)
        add_to_steam.setEnabled(self.window.configured)
        remove_from_steam = menu.addAction("Remove from Steam", self._remove_from_steam)
        remove_from_steam.setToolTip("Takes the non-Steam game Add to Steam made back out. Close Steam first.")
        remove_from_steam.setEnabled(self.window.configured)
        menu.addSeparator()
        show_logs = menu.addAction("Show Logs")
        show_logs.setCheckable(True)
        show_logs.setChecked(self._logs_shown)
        show_logs.setToolTip("Shows the log of what Start and Stop are doing at the bottom of this page.")
        show_logs.toggled.connect(self.set_logs_shown)
        menu.addSeparator()
        forget = menu.addAction("Make Next Start a Full Restart", self._clear_resume_state)
        forget.setToolTip("Starts Android from scratch next time instead of resuming where it left off. Try it if iiSU seems stuck.")
        return menu

    def set_logs_shown(self, shown: bool) -> None:
        """Shows or hides the log drawer, and remembers it for next time."""
        self._logs_shown = shown
        self.log_drawer.setVisible(shown)
        self.window.set_home_show_logs(shown)

    # == Home state ==

    def refresh_home_state(self) -> None:
        self.setup_intro_label.setText(
            "The settings file is damaged, so Community-iiSU-PC can't start. Your Android VM, "
            "iiSU and your games are not affected."
            if self.window.config_problem else
            "Not set up yet. Setup installs a self-contained Android VM and patches your copy "
            "of iiSU to hand game launches to PC emulators."
        )
        self.setup_intro_label.setVisible(not self.window.configured)
        self.status_row.setVisible(self.window.configured)
        self._refresh_primary_button()
        self._refresh_resume_reason()

    def refresh_resume_reason(self) -> None:
        self._refresh_resume_reason()

    def _refresh_resume_reason(self) -> None:
        """What the next start will be, as the tooltip of the menu button
        next to Start."""
        self.advanced_button.setToolTip(self._next_start_text())

    def _next_start_text(self) -> str:
        if not self.window.configured:
            return LAUNCH_OPTIONS_TOOLTIP
        reasons = start_iisu_pc.cold_boot_reasons(self.window.config_data)
        if not reasons:
            return "Next start: quick (resumes where Android left off)"
        if len(reasons) == 1:
            return f"Next start: full restart ({reasons[0]})"
        # One reason per line: together they are too long for one.
        return "\n".join([f"Next start: full restart ({len(reasons)} things changed)", *reasons])

    def _clear_resume_state(self) -> None:
        reply = QMessageBox.question(
            self,
            "Make Next Start a Full Restart",
            "The next start will start Android from scratch instead of resuming where it left off. "
            "It takes a little longer.\n\n"
            "Use it if iiSU seems stuck after starting. Your settings, games and Android are unaffected.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        start_iisu_pc.clear_boot_fingerprint()
        self._refresh_resume_reason()
        QMessageBox.information(self, "Make Next Start a Full Restart", "Done. The next start will be a full restart.")

    def _refresh_primary_button(self) -> None:
        if self.window.config_problem:
            self.primary_button.setText("Repair Settings")
            self.primary_button.setEnabled(True)
        elif not self.window.configured:
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
            self.primary_button.setText("Repatch iiSU" if self.window.needs_repatch() else "Start iiSU")
            self.primary_button.setEnabled(not self.busy)
        self._restyle_primary_button()

    def refresh_primary_button(self) -> None:
        self._refresh_primary_button()

    def _restyle_primary_button(self) -> None:
        """Stop gets the quiet look, everything that starts something the gradient."""
        mode = "stop" if self.primary_button.text() == "Stop" else "go"
        if self.primary_button.property("mode") != mode:
            self.primary_button.setProperty("mode", mode)
            self.primary_button.style().unpolish(self.primary_button)
            self.primary_button.style().polish(self.primary_button)
        self._primary_glow.setEnabled(mode == "go" and self.primary_button.isEnabled())

    def _on_primary_click(self) -> None:
        if self.window.config_problem:
            self.window.offer_config_recovery()
        elif not self.window.configured:
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

    def _start(self, headless: bool = False) -> None:
        if self.busy:
            return
        # A project update changed the patch: iiSU isn't started until it's
        # repatched, from this button and the Boot Headless menu alike.
        if self.window.needs_repatch():
            self.window.open_repatch_wizard()
            return
        self._set_busy(True)
        self.stage_label.setText("Starting in the background..." if headless else "Starting...")
        self._append_log("\n=== Start (headless, no window) ===\n" if headless else "\n=== Start ===\n")
        self._guarded_signals = run_in_background(
            self._run_guarded, self._on_guarded_done, self._on_guarded_error, lambda: start_iisu_pc.main(headless=headless)
        )

    def _start_headless(self) -> None:
        self._start(headless=True)

    def _stop(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.stage_label.setText("Stopping...")
        self._append_log("\n=== Stop ===\n")
        self._guarded_signals = run_in_background(self._run_guarded, self._on_guarded_done, self._on_guarded_error, stop_iisu_pc.main)

    def _sync_library_now(self) -> None:
        """The start's library sync, run on demand while iiSU is open, then
        the rescan (see bridge/library_rescan.py). The log drawer opens so
        there's something to watch."""
        if self.busy:
            return
        self.set_logs_shown(True)
        self._set_busy(True)
        self._append_log("\n[manager] syncing your ROM library into Android...\n")
        self._guarded_signals = run_in_background(
            self._run_guarded, self._on_guarded_done, self._on_guarded_error, self._sync_and_rescan
        )

    @staticmethod
    def _sync_and_rescan() -> None:
        import library_rescan
        import sync_library
        from shared import adb_client

        try:
            sync_library.main()
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise
        if not library_rescan.pending():
            print("[manager] nothing new to rescan.")
            return
        if library_rescan.rescan_when_ready(adb_client.shell, timeout=30, settle=0):
            library_rescan.clear()

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
        open_uri(str(roms_dir))

    def _open_logs(self) -> None:
        bridge_dir = Path(__file__).resolve().parent.parent.parent.parent / "bridge"
        open_uri(str(bridge_dir))

    def _recreate_desktop_shortcut(self) -> None:
        from bridge.ui.dialogs.icon_choice_dialog import IconChoiceDialog

        dialog = IconChoiceDialog(self.window.config_data.get("icon_choice"), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        choice = dialog.choice()
        self.window.set_icon_choice(choice)
        # A one-shot menu action now rather than its own always-visible
        # button, so the advanced-options button itself is the "busy"
        # indicator that stops a second click starting a second run.
        self.advanced_button.setEnabled(False)
        self._shortcut_signals = run_in_background(
            self._recreate_shortcut_worker, self._on_shortcut_recreated, self._on_shortcut_error, choice,
        )

    def _recreate_shortcut_worker(self, choice: str):
        import create_shortcut

        return create_shortcut.create_desktop_shortcut(icon_choice=choice)

    def _on_shortcut_recreated(self, path) -> None:
        self.advanced_button.setEnabled(True)
        QMessageBox.information(self, "Shortcut", f"Desktop shortcut created:\n{path}")

    def _on_shortcut_error(self, message: str) -> None:
        self.advanced_button.setEnabled(True)
        QMessageBox.critical(self, "Shortcut", f"Couldn't create the desktop shortcut:\n{message}")

    # == Steam shortcut ==

    def _add_to_steam(self) -> None:
        from bridge.services import steam_shortcuts_service as steam

        try:
            results = steam.add_to_steam()
        except steam.SteamShortcutError as e:
            QMessageBox.warning(self, "Add to Steam", str(e))
            return
        except OSError as e:
            QMessageBox.critical(self, "Add to Steam", f"Couldn't write Steam's shortcut list:\n\n{e}")
            return
        words = {"added": "Added", "updated": "Updated"}
        summary = "\n".join(f"{words[outcome]}: {path}" for path, outcome in results)
        QMessageBox.information(
            self, "Add to Steam",
            f"{summary}\n\nLook for \"{steam.SHORTCUT_NAME}\" in Steam's library. "
            "The old shortcut list was saved as a .bak file.",
        )

    def _remove_from_steam(self) -> None:
        from bridge.services import steam_shortcuts_service as steam

        try:
            changed = steam.remove_from_steam()
        except steam.SteamShortcutError as e:
            QMessageBox.warning(self, "Remove from Steam", str(e))
            return
        except OSError as e:
            QMessageBox.critical(self, "Remove from Steam", f"Couldn't write Steam's shortcut list:\n\n{e}")
            return
        QMessageBox.information(self, "Remove from Steam", "Removed it from Steam." if changed else "It wasn't in Steam's shortcut list.")

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
        # A damaged file counts as not configured, the same as the window
        # sees it. Counting it as configured made this poll reload and rebuild
        # every settings page every two seconds for as long as it stayed damaged.
        now_configured = CONFIG_PATH.is_file() and bridge_config.config_problem() is None
        return (avd_up, bridge_up, now_configured)

    def refresh_problem(self) -> None:
        problem = launch_problems.load()
        if problem is None:
            self.problem_card.hide()
            return
        when = launch_problems.describe_time(float(problem.get("time", 0)))
        title = problem.get("title") or ""
        source = f" ({title})" if title and title != "Couldn't open that game" else ""
        self.problem_title.setText(f"A game couldn't open {when}{source}")
        self.problem_text.setText(problem.get("message", ""))
        self.problem_text.setToolTip(problem.get("detail", ""))
        self.problem_card.show()

    def _dismiss_problem(self) -> None:
        launch_problems.dismiss()
        self.problem_card.hide()

    def _apply_status(self, result) -> None:
        self.refresh_problem()
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
            if self.window.config_problem and not self._hidden_for_setup:
                self.window.offer_config_recovery()

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
        VM/bridge are still running in the background."""
        if self.window.last_avd_up or self.window.last_bridge_up:
            reply = QMessageBox.question(
                self.window,
                "Community-iiSU-PC is still running",
                "The VM or bridge is still running in the background.\n\n"
                "Closing this window won't stop it. Use Stop first to shut everything down.\n\n"
                "Close anyway?",
            )
            return reply == QMessageBox.StandardButton.Yes
        return True
