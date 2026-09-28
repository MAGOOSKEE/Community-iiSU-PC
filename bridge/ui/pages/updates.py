"""Updates page: shows the installed Community-iiSU-PC version, lets the
user check GitHub for a newer one, and install it on demand. Backed by
updater.py (the same module start_iisu_pc.py's opt-in auto-update uses)
and diagnostics_service.check_for_updates_detailed() for the read-only
check. Installing restarts the Manager itself (see main_window.py's
_restart_after_update) since Python can't hot-swap the code it already
loaded into this process."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

import updater
from bridge.services import diagnostics_service as svc
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, TEXT_DIM


class UpdatesPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window
        self._checked_once = False
        self._check_inflight = False
        self._install_inflight = False
        self._update_available = False
        self._check_signals = None
        self._install_signals = None

        self.add_header("Updates", "Check for and install the latest Community-iiSU-PC release.")

        version_card = Card()
        version_layout = QVBoxLayout(version_card)
        version = updater.get_installed_version() or "unknown"
        install_kind = "Git checkout" if updater.is_git_checkout() else "Packaged release"
        self.version_label = QLabel(f"Installed version: {version}")
        self.version_label.setFont(Fonts.heading())
        version_layout.addWidget(self.version_label)
        kind_label = QLabel(f"Install type: {install_kind}")
        kind_label.setStyleSheet(f"color: {TEXT_DIM};")
        version_layout.addWidget(kind_label)
        self.body_layout.addWidget(version_card)

        actions_card = Card()
        actions_layout = QVBoxLayout(actions_card)

        button_row = QHBoxLayout()
        self.check_button = QPushButton("Check for Updates")
        self.check_button.setObjectName("ghost")
        self.check_button.clicked.connect(self.check_now)
        button_row.addWidget(self.check_button)

        self.install_button = QPushButton("Install Update")
        self.install_button.setObjectName("accent")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self._install_now)
        button_row.addWidget(self.install_button)
        button_row.addStretch(1)
        actions_layout.addLayout(button_row)

        self.status_label = QLabel("Check for Updates hasn't been run yet.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        actions_layout.addWidget(self.status_label)
        self.body_layout.addWidget(actions_card)

        note = QLabel(
            "Installing downloads the update, applies it to this install, and restarts "
            "Community-iiSU-PC Manager to run the new code. Stop Community-iiSU-PC first if it's running."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(note)
        self.body_layout.addStretch(1)

    def on_shown(self) -> None:
        if not self._checked_once:
            self.check_now()

    def set_startup_status(self, message: str, update_available: bool) -> None:
        """Called by ManagerWindow after its own startup check finishes,
        so this page reflects it even if the user never opens the tab."""
        self._checked_once = True
        self._apply_check_result(svc.UpdateCheckResult(message, update_available))

    def check_now(self) -> None:
        if self._check_inflight:
            return
        self._checked_once = True
        self._check_inflight = True
        self.check_button.setEnabled(False)
        self.status_label.setText("Checking for updates...")
        self._check_signals = run_in_background(svc.check_for_updates_detailed, self._apply_check_result, self._check_error)

    def _apply_check_result(self, result) -> None:
        self._check_inflight = False
        self.check_button.setEnabled(True)
        self._update_available = result.update_available
        self.install_button.setEnabled(result.update_available and not self._install_inflight)
        self.status_label.setText(result.message)

    def _check_error(self, message: str) -> None:
        self._check_inflight = False
        self.check_button.setEnabled(True)
        self.status_label.setText(f"Update check failed: {message}")

    def _install_now(self) -> None:
        if self._install_inflight or not self._update_available:
            return
        if self.window.last_avd_up or self.window.last_bridge_up:
            QMessageBox.warning(
                self,
                "Community-iiSU-PC is running",
                "Stop Community-iiSU-PC before installing an update, an update in progress "
                "can conflict with files a running session still has open.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Install Update",
            "This downloads and applies the update, then restarts Community-iiSU-PC Manager. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._install_inflight = True
        self.check_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.status_label.setText("Installing update...")
        self._install_signals = run_in_background(updater.install_update, self._on_installed, self._install_error)

    def _on_installed(self, result) -> None:
        message, applied = result
        self._install_inflight = False
        self.check_button.setEnabled(True)
        self.status_label.setText(message)
        if applied:
            self.install_button.setEnabled(False)
            self._update_available = False
            self.window.restart_after_update()
        else:
            self.install_button.setEnabled(self._update_available)

    def _install_error(self, message: str) -> None:
        self._install_inflight = False
        self.check_button.setEnabled(True)
        self.install_button.setEnabled(self._update_available)
        self.status_label.setText(f"Install failed: {message}")
