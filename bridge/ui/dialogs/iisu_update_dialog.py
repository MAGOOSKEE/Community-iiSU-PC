"""Progress dialog for re-patching/reinstalling iiSU (see
bridge/services/iisu_update_service.py), opened from Diagnostics page's
"Update iiSU Now" / "Use a Different APK..." actions.

Mirrors bridge/ui/setup_app.py's stdout-redirect + on_stage pattern (same
underlying setup_wizard.py machinery, update_iisu() instead of
run_setup()), scaled down into a dialog rather than a whole separate
window/process: this is a much shorter operation than first-time setup,
and belongs inside the Manager instead of launching as its own process.
"""

import sys
import traceback

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from bridge.services import iisu_update_service as svc
from bridge.ui.workers.log_stream import LogStreamRedirector
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, GREEN, RED, SPACING_MD, SPACING_SM


class IisuUpdateDialog(QDialog):
    stage_changed = Signal(str, int, int)

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update iiSU")
        self.resize(600, 420)
        self._source = source
        self.running = False
        self.succeeded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        self.status_label = QLabel("Starting...")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate until a stage/result is known
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(Fonts.mono())
        layout.addWidget(self.log_text, stretch=1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.accept)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self._log_stream = LogStreamRedirector()
        self._log_stream.text_written.connect(self._append_log)
        self.stage_changed.connect(self._apply_stage)
        self._signals = None

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._signals = run_in_background(self._run, self._on_finished, None, self._source)

    def _run(self, source) -> tuple[bool, str]:
        # Redirected for the duration of this background-thread call only;
        # setup_wizard.update_iisu() (like run_setup()) reports progress via
        # plain print(), same as setup_app.py's identical redirect around
        # run_setup(). sys.stdout is process-global, but nothing else on
        # this thread pool runs concurrently with this one dialog's update.
        old_stdout = sys.stdout
        sys.stdout = self._log_stream
        try:
            message = svc.apply_iisu_update(source, on_stage=self._on_stage)
            return True, message
        except Exception as e:  # noqa: BLE001; reported in the dialog, not swallowed
            print(f"\n[update] FAILED: {e}\n")
            print(traceback.format_exc())
            return False, str(e)
        finally:
            sys.stdout = old_stdout

    def _on_stage(self, label: str, index: int, total: int) -> None:
        # Called from the worker thread; emitting a Qt signal from here is
        # safe the same way LogStreamRedirector's write() is, the queued
        # connection delivers _apply_stage's call on the main thread.
        self.stage_changed.emit(label, index, total)

    def _apply_stage(self, label: str, index: int, total: int) -> None:
        self.status_label.setText(f"Step {index}/{total}: {label}...")

    def _append_log(self, text: str) -> None:
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)

    def _on_finished(self, result: tuple[bool, str]) -> None:
        self.running = False
        success, message = result
        self.succeeded = success
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if success else 0)
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {GREEN if success else RED};")
        self.close_button.setEnabled(True)
