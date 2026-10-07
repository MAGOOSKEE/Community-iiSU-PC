"""Updates page: shows the installed Community-iiSU-PC version, lets the
user check GitHub for a newer one and install it on demand, controls the
opt-in "apply on startup" setting, and (separately) checks/installs
updates to iiSU itself. All of this used to be split across this page
and the Diagnostics page; it lives here now so Diagnostics stays purely
read-only checks. Backed by updater.py (the same module start_iisu_pc.py's
opt-in auto-update uses), diagnostics_service.check_for_updates_detailed()
for the read-only Community-iiSU-PC check, and iisu_update_service for
iiSU's own. Installing a Community-iiSU-PC update restarts the Manager
itself (see main_window.py's restart_after_update) since Python can't
hot-swap the code it already loaded into this process."""

from pathlib import Path

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

import updater
from bridge.services import diagnostics_service as svc
from bridge.services import iisu_update_service as iisu_svc
from bridge.ui.dialogs.iisu_update_dialog import IisuUpdateDialog
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, TEXT_DIM
from bridge_config import save_config


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
        actions_top = QHBoxLayout()
        actions_top.addWidget(QLabel("Community-iiSU-PC Updates"))
        actions_top.addStretch(1)
        self.auto_updates_check = QCheckBox("Automatically apply updates on startup")
        self.auto_updates_check.setChecked(bool(window.config_data.get("auto_updates", False)))
        self.auto_updates_check.toggled.connect(self._set_auto_updates)
        actions_top.addWidget(self.auto_updates_check)
        actions_layout.addLayout(actions_top)

        auto_updates_note = QLabel(
            "Off is recommended for customized installations. When enabled, startup checks apply "
            "an available update automatically (fast-forwarding a Git checkout, or downloading a "
            "newer release) and restart Community-iiSU-PC Manager to run it."
        )
        auto_updates_note.setWordWrap(True)
        auto_updates_note.setStyleSheet(f"color: {TEXT_DIM};")
        actions_layout.addWidget(auto_updates_note)

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

        self.notes_label = QLabel("What's new")
        self.notes_label.setVisible(False)
        actions_layout.addWidget(self.notes_label)
        self.notes_text = QPlainTextEdit()
        self.notes_text.setReadOnly(True)
        self.notes_text.setFixedHeight(150)
        self.notes_text.setVisible(False)
        actions_layout.addWidget(self.notes_text)
        self.body_layout.addWidget(actions_card)

        note = QLabel(
            "Installing downloads the update, applies it to this install, and restarts "
            "Community-iiSU-PC Manager to run the new code. Stop Community-iiSU-PC first if it's running."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(note)

        iisu_card = Card()
        iisu_layout = QVBoxLayout(iisu_card)
        iisu_top = QHBoxLayout()
        iisu_top.addWidget(QLabel("iiSU App Updates"))
        iisu_top.addStretch(1)
        iisu_layout.addLayout(iisu_top)

        iisu_note = QLabel(
            "iiSU itself is a separate, third-party app with no auto-update channel here: checking "
            "only compares versions against its official GitHub releases, nothing downloads on its own. "
            "Updating re-patches and reinstalls it on the AVD, this stops iiSU (and any running game) "
            "if it's currently open."
        )
        iisu_note.setWordWrap(True)
        iisu_note.setStyleSheet(f"color: {TEXT_DIM};")
        iisu_layout.addWidget(iisu_note)

        iisu_actions = QHBoxLayout()
        iisu_check_button = QPushButton("Check for iiSU Updates")
        iisu_check_button.setObjectName("ghost")
        iisu_check_button.clicked.connect(self._check_for_iisu_update)
        iisu_actions.addWidget(iisu_check_button)
        self.iisu_update_now_button = QPushButton("Update iiSU Now")
        self.iisu_update_now_button.setObjectName("accent")
        self.iisu_update_now_button.setEnabled(False)
        self.iisu_update_now_button.clicked.connect(self._update_iisu_now)
        iisu_actions.addWidget(self.iisu_update_now_button)
        iisu_manual_button = QPushButton("Use a Different APK...")
        iisu_manual_button.setObjectName("ghost")
        iisu_manual_button.setToolTip(
            "Patch and install any APK you point at directly, bypassing the version check above. "
            "For an official pre-release shared before it's on the releases page, for example."
        )
        iisu_manual_button.clicked.connect(self._pick_manual_iisu_apk)
        iisu_actions.addWidget(iisu_manual_button)
        iisu_compat_button = QPushButton("Check an APK...")
        iisu_compat_button.setObjectName("ghost")
        iisu_compat_button.setToolTip(
            "Inspect an iiSU APK and report whether this tool can patch it, without installing "
            "anything. Takes about a minute. Worth doing on a new pre-release before updating to it."
        )
        iisu_compat_button.clicked.connect(self._check_apk_compatibility)
        iisu_actions.addWidget(iisu_compat_button)
        iisu_layout.addLayout(iisu_actions)

        self.iisu_update_status_label = QLabel("Check for iiSU updates hasn't been run yet.")
        self.iisu_update_status_label.setWordWrap(True)
        self.iisu_update_status_label.setStyleSheet(f"color: {TEXT_DIM};")
        iisu_layout.addWidget(self.iisu_update_status_label)
        self.body_layout.addWidget(iisu_card)

        self._iisu_check_inflight = False
        self._iisu_check_signals = None
        self._pending_iisu_download_url = None

        self.body_layout.addStretch(1)

    def on_shown(self) -> None:
        if not self._checked_once:
            self.check_now()

    def set_startup_status(self, message: str, update_available: bool) -> None:
        """Called by ManagerWindow after its own startup check finishes,
        so this page reflects it even if the user never opens the tab."""
        self._checked_once = True
        self._apply_check_result(svc.UpdateCheckResult(message, update_available))

    # == Community-iiSU-PC updates ==

    def _set_auto_updates(self, enabled: bool) -> None:
        self.window.config_data["auto_updates"] = enabled
        try:
            save_config(self.window.config_data)
        except Exception as exc:
            self.auto_updates_check.blockSignals(True)
            self.auto_updates_check.setChecked(not enabled)
            self.auto_updates_check.blockSignals(False)
            self.window.config_data["auto_updates"] = not enabled
            QMessageBox.critical(self, "Community-iiSU-PC Updates", f"Couldn't save the update setting:\n\n{exc}")
            return
        state = "enabled" if enabled else "disabled"
        self.status_label.setText(f"Automatic startup updates are {state}.")

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
        notes = result.notes if result.update_available else ""
        self.notes_text.setPlainText(notes)
        self.notes_label.setVisible(bool(notes))
        self.notes_text.setVisible(bool(notes))

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

    # == iiSU app updates ==

    def _check_for_iisu_update(self) -> None:
        if self._iisu_check_inflight:
            return
        self._iisu_check_inflight = True
        self._pending_iisu_download_url = None
        self.iisu_update_now_button.setEnabled(False)
        self.iisu_update_status_label.setText("Checking iiSU's releases (read-only)...")
        self._iisu_check_signals = run_in_background(iisu_svc.check_for_iisu_update, self._apply_iisu_check, self._iisu_check_error)

    def _apply_iisu_check(self, result) -> None:
        self._iisu_check_inflight = False
        self.iisu_update_status_label.setText(result.message)
        if result.update_available and result.latest is not None:
            self._pending_iisu_download_url = result.latest.download_url
            self.iisu_update_now_button.setEnabled(True)

    def _iisu_check_error(self, message: str) -> None:
        self._iisu_check_inflight = False
        self.iisu_update_status_label.setText(f"iiSU update check failed: {message}")

    def _confirm_iisu_update(self) -> bool:
        reply = QMessageBox.question(
            self,
            "Update iiSU",
            "This re-patches and reinstalls iiSU on the AVD. It stops iiSU (and any running game) if "
            "it's currently open, and can take a few minutes. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _update_iisu_now(self) -> None:
        if self._pending_iisu_download_url is None:
            return
        if not self._confirm_iisu_update():
            return
        self._run_iisu_update(self._pending_iisu_download_url)

    def _check_apk_compatibility(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select an iiSU APK to check", "", "Android APK (*.apk)")
        if not path:
            return
        self.iisu_update_status_label.setText(f"Checking {Path(path).name} (about a minute)...")
        self._compat_signals = run_in_background(self._compat_worker, self._on_compat_done, self._on_compat_error, path)

    @staticmethod
    def _compat_worker(path: str) -> str:
        import patch_check

        return patch_check.format_report(patch_check.check_apk(Path(path)))

    def _on_compat_done(self, report_text: str) -> None:
        self.iisu_update_status_label.setText(report_text.splitlines()[-1])
        box = QMessageBox(self)
        box.setWindowTitle("iiSU APK compatibility")
        box.setText(report_text.splitlines()[-1])
        box.setDetailedText(report_text)
        box.exec()

    def _on_compat_error(self, message: str) -> None:
        self.iisu_update_status_label.setText(f"Couldn't check that APK: {message}")

    def _pick_manual_iisu_apk(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select an iiSU APK", "", "Android APK (*.apk)")
        if not path:
            return
        if not self._confirm_iisu_update():
            return
        # QFileDialog always returns a plain str, same as the download-URL
        # case; apply_iisu_update() tells them apart by type (str = URL to
        # fetch, Path = already-local file), so this local path needs
        # wrapping here or it gets handed to urlopen() instead.
        self._run_iisu_update(Path(path))

    def _run_iisu_update(self, source) -> None:
        # A modal dialog with its own live log/step display, rather than
        # this page's one-line status label, this can run for several
        # minutes (decompile, patch, rebuild, sign, boot the AVD, install),
        # worth seeing progress on rather than staring at a static message.
        dialog = IisuUpdateDialog(source, parent=self.window)
        dialog.start()
        dialog.exec()
        if dialog.succeeded:
            self._pending_iisu_download_url = None
        self.iisu_update_status_label.setText(dialog.status_label.text())
