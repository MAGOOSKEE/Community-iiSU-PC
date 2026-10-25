"""Settings > Storage: what is using the Android VM's internal storage (a
colour-coded bar), how big its file is on your drive, cleaning up old copies,
and recreating the VM at a different size. Backed by
bridge/services/vm_storage_service.py."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

import sys
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from bridge.services import android_backup_service as backup_svc
from bridge.services import vm_storage_service as svc
from bridge.services.android_storage_service import adb_command, format_size
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.storage_bar import StorageBar
from bridge.ui.workers.task_runner import run_in_background
from shared import vm_storage, vm_storage_breakdown as bd
from shared.qt_theme import Fonts, GREEN, RED, TEXT_DIM
from shared.vm_backend import BACKEND_AVD, resolve_backend

RESIZE_WARNING = (
    "Recreating replaces the VM's storage with an empty one at the new size, reinstalls iiSU and restores "
    "the backup (title art and media). Not restored: iiSU's settings, favorites and play history, its folder "
    "access (pick your ROM folder again), and anything else you installed in the VM. The old storage stays "
    "on your drive until you delete it below."
)


class _LineSignal:
    """A stand-in for sys.stdout that forwards each printed line to a Qt signal,
    so Setup's and Stop's progress prints reach the status label."""

    def __init__(self, emit):
        self._emit = emit
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._emit(line.strip())
        return len(text)

    def flush(self) -> None:
        pass


class StoragePage(PageBase):
    progress = Signal(str)

    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window
        self._signals = None
        self._busy = False
        self.progress.connect(self._show_progress)

        self.add_header("Storage", "What uses the VM's internal storage, and how to change its size.")

        # == Inside the VM ==
        inside = Card()
        inside_layout = QVBoxLayout(inside)
        heading = QLabel("Inside the VM")
        heading.setFont(Fonts.heading())
        inside_layout.addWidget(heading)
        self.summary_label = QLabel("")
        inside_layout.addWidget(self.summary_label)
        self.bar = StorageBar()
        inside_layout.addWidget(self.bar)
        self.legend = QGridLayout()
        self.legend.setColumnMinimumWidth(1, 300)
        self.legend.setColumnStretch(4, 1)
        self.legend.setHorizontalSpacing(14)
        inside_layout.addLayout(self.legend)
        self.top_apps_label = QLabel("")
        self.top_apps_label.setWordWrap(True)
        self.top_apps_label.setStyleSheet(f"color: {TEXT_DIM};")
        inside_layout.addWidget(self.top_apps_label)
        row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("ghost")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        inside_layout.addLayout(row)
        self.body_layout.addWidget(inside)

        # == On your drive ==
        drive = Card()
        drive_layout = QVBoxLayout(drive)
        drive_heading = QLabel("On your drive")
        drive_heading.setFont(Fonts.heading())
        drive_layout.addWidget(drive_heading)
        self.drive_label = QLabel("")
        self.drive_label.setWordWrap(True)
        drive_layout.addWidget(self.drive_label)
        drive_note = QLabel(
            "The storage file grows as the VM fills and never shrinks, so deleting files in the VM doesn't "
            "free drive space. Recreating the VM does."
        )
        drive_note.setWordWrap(True)
        drive_note.setStyleSheet(f"color: {TEXT_DIM};")
        drive_layout.addWidget(drive_note)
        drive_buttons = QHBoxLayout()
        self.old_copies_button = QPushButton("Delete old copies...")
        self.old_copies_button.setObjectName("ghost")
        self.old_copies_button.clicked.connect(self._delete_old_copies)
        drive_buttons.addWidget(self.old_copies_button)
        drive_buttons.addStretch(1)
        drive_layout.addLayout(drive_buttons)
        self.body_layout.addWidget(drive)

        # == Size ==
        size_card = Card()
        size_layout = QVBoxLayout(size_card)
        size_heading = QLabel("Internal storage size")
        size_heading.setFont(Fonts.heading())
        size_layout.addWidget(size_heading)
        warning = QLabel(RESIZE_WARNING)
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {TEXT_DIM};")
        size_layout.addWidget(warning)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("New size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(vm_storage.MIN_DATA_PARTITION_GB, vm_storage.MAX_DATA_PARTITION_GB)
        self.size_spin.setValue(vm_storage.DEFAULT_DATA_PARTITION_GB)
        self.size_spin.setSuffix(" GB")
        size_row.addWidget(self.size_spin)
        self.resize_button = QPushButton("Recreate VM at this size...")
        self.resize_button.setObjectName("accent")
        self.resize_button.clicked.connect(self._resize)
        size_row.addWidget(self.resize_button)
        size_row.addStretch(1)
        size_layout.addLayout(size_row)
        self.body_layout.addWidget(size_card)

        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)  # indeterminate: a resize has no honest percentage
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setFixedHeight(4)
        self.busy_bar.hide()
        self.body_layout.addWidget(self.busy_bar)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.body_layout.addWidget(self.status_label)
        self.body_layout.addStretch(1)

        self._after_delete = None
        self._host = None
        self._usage: vm_storage.StorageUsage | None = None
        self._update_buttons()

    # == Helpers ==

    def _avd_name(self) -> str:
        return self.window.config_data.get("avd_name", "iisuwin")

    def _is_avd_backend(self) -> bool:
        return resolve_backend(self.window.config_data) == BACKEND_AVD

    def _vm_running(self) -> bool:
        return bool(self.window.last_avd_up)

    def _set_status(self, text: str, color: str | None = None) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color or TEXT_DIM};")

    def _show_progress(self, text: str) -> None:
        self._set_status(text)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.busy_bar.setVisible(busy)
        self._update_buttons()

    def _update_buttons(self) -> None:
        avd = self._is_avd_backend()
        idle = not self._busy
        self.refresh_button.setEnabled(idle)
        self.resize_button.setEnabled(idle and avd)
        self.size_spin.setEnabled(idle and avd)
        self.old_copies_button.setEnabled(idle and bool(self._host and self._host.old_copies))

    def _run(self, work, done, *args) -> None:
        """Runs work(*args) in the background with printed progress forwarded
        to the status label; done(result) and errors come back on the UI thread."""

        def job():
            old_stdout = sys.stdout
            sys.stdout = _LineSignal(self.progress.emit)
            try:
                return work(*args)
            finally:
                sys.stdout = old_stdout

        self._set_busy(True)
        self._signals = run_in_background(job, done, self._on_error)

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(message, RED)
        QMessageBox.critical(self, "Storage", message)
        self._reload_host()

    # == Showing ==

    def on_shown(self) -> None:
        self._reload_host()
        if self._host.configured_gb and not self._busy:
            self.size_spin.setValue(int(round(self._host.configured_gb)))
        self.refresh()

    def _reload_host(self) -> None:
        self._host = svc.host_info(self._avd_name())
        info = self._host
        configured = f"{info.configured_gb:g} GB" if info.configured_gb else "unknown"
        text = f"Storage file: {format_size(info.data_bytes)} on your drive, up to {configured}."
        if self._usage is not None:
            held = info.data_bytes - self._usage.used_kb * 1024
            if held > 512 * 1024 * 1024:
                text += f"\nAbout {format_size(held)} is freed space the file still holds."
        if info.old_copies:
            text += f"\nOld copies: {len(info.old_copies)}, using {format_size(info.old_copies_bytes)}."
        self.drive_label.setText(text)
        self._update_buttons()

    def refresh(self) -> None:
        if not self._is_avd_backend():
            self.summary_label.setText("Waydroid manages its own storage.")
            return
        if not self._vm_running():
            self.summary_label.setText("Start the VM to see what uses its storage.")
            self.bar.set_segments([])
            self._fill_legend([])
            self.top_apps_label.setText("")
            self._update_buttons()
            return
        self.summary_label.setText("Measuring...")
        self._run(lambda: svc.collect_breakdown(adb_command, self.progress.emit), self._apply_breakdown)

    def _apply_breakdown(self, breakdown: bd.Breakdown) -> None:
        self._set_busy(False)
        self._set_status("")
        self._usage = breakdown.usage
        self._reload_host()
        self.summary_label.setText(vm_storage.describe(breakdown.usage))
        self.bar.set_segments(breakdown.segments)
        self._fill_legend(breakdown.segments, breakdown.usage.total_kb)
        if breakdown.top_apps:
            names = ", ".join(f"{name} ({bd.format_kb(kb)})" for name, kb in breakdown.top_apps[:5] if kb > 0)
            self.top_apps_label.setText(f"Largest apps: {names}")
        else:
            self.top_apps_label.setText("Per-app sizes unavailable. Apps are counted under Android system.")

    def _fill_legend(self, segments: list[bd.Segment], total_kb: int = 0) -> None:
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for row, segment in enumerate(segment for segment in segments if segment.kb > 0):
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(f"background-color: {segment.color}; border-radius: 4px;")
            name = QLabel(segment.label)
            size = QLabel(bd.format_kb(segment.kb))
            percent = QLabel(f"{round(100 * segment.kb / total_kb)}%" if total_kb else "")
            percent.setStyleSheet(f"color: {TEXT_DIM};")
            for column, widget in enumerate((swatch, name, size, percent)):
                self.legend.addWidget(widget, row, column)

    # == Old copies ==

    def _delete_old_copies(self) -> None:
        if not self._host or not self._host.old_copies:
            return
        listing = "\n".join(f"- {copy.path.name} ({format_size(copy.size_bytes)})" for copy in self._host.old_copies)
        reply = QMessageBox.question(
            self, "Delete old copies",
            f"Permanently delete these old VM copies?\n\n{listing}\n\nThey hold the storage from before earlier resizes. "
            "This can't be undone.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run(self._delete_work, self._on_old_copies_deleted, list(self._host.old_copies))

    def _delete_work(self, copies) -> int:
        freed = 0
        for copy in copies:
            svc.delete_old_copy(copy.path, self._avd_name())
            freed += copy.size_bytes
        return freed

    def _on_old_copies_deleted(self, freed: int) -> None:
        self._set_busy(False)
        self._reload_host()
        self._set_status(f"Deleted old copies, freed {format_size(freed)}.", GREEN)
        follow_up, self._after_delete = self._after_delete, None
        if follow_up:
            follow_up()

    # == Resize ==

    def _resize(self) -> None:
        new_gb = self.size_spin.value()
        if not self._vm_running():
            QMessageBox.information(
                self, "Recreate VM",
                "Start the VM first, so iiSU's title art and media can be backed up before anything changes.",
            )
            return
        used_gb = self._usage.used_kb / 1024 / 1024 if self._usage else 0
        if self._usage and new_gb < used_gb * 1.2:
            QMessageBox.warning(
                self, "Recreate VM",
                f"{used_gb:.1f} GB is in use, so {new_gb} GB would start nearly full. Pick at least {int(used_gb * 1.2) + 1} GB.",
            )
            return
        reply = QMessageBox.warning(
            self, "Recreate the VM?",
            f"Recreate the VM with {new_gb} GB of internal storage?\n\n{RESIZE_WARNING}\n\n"
            "You'll choose where to save the backup next. Nothing changes until it's saved and checked.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save the backup before recreating the VM", backup_svc.default_backup_filename(), "iiSU data backup (*.zip)"
        )
        if not path:
            return
        self._set_status("Starting...")
        self._run(self._resize_work, self._on_resized, new_gb, Path(path))

    def _resize_work(self, new_gb: int, backup_path: Path):
        config = self.window.config_data
        steps = svc.real_steps(self._avd_name(), lean=bool(config.get("lean_vm", False)))
        return svc.resize(svc.avd_dir(self._avd_name()), new_gb, backup_path, steps, self.progress.emit)

    def _on_resized(self, result: svc.ResizeResult) -> None:
        self._set_busy(False)
        self._reload_host()
        lines = [f"The VM now has {result.new_gb} GB of internal storage."]
        if result.restore_error:
            lines.append(f"Restoring iiSU's data failed: {result.restore_error}. Use Backup & Restore > Restore iiSU Data with your backup.")
        elif result.restore_outcome:
            lines.append("iiSU's title art and media were restored.")
        self._set_status(lines[0], GREEN)
        QMessageBox.information(self, "Recreate VM", "\n\n".join(lines))
        # The old storage is only needed to undo this by hand, and can be most of a
        # drive's worth of space, so offer to drop it unless the restore had a problem.
        if result.old_copy and result.old_copy.is_dir() and not result.restore_error:
            self._offer_delete_old_storage(result.old_copy)
        else:
            self._offer_relink_rom_folder()

    def _offer_delete_old_storage(self, old_copy: Path) -> None:
        size = svc.folder_size(old_copy)
        reply = QMessageBox.question(
            self, "Delete the old storage?",
            f"Free {format_size(size)} by deleting the old storage now?\n\n"
            "It's only needed to undo this resize by hand. This can't be undone.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._set_status(f"Old storage kept in {old_copy.name}. Delete it here when you're happy.")
            self._offer_relink_rom_folder()
            return
        self._after_delete = self._offer_relink_rom_folder
        self._run(self._delete_work, self._on_old_copies_deleted, [svc.OldCopy(old_copy, size)])

    def _offer_relink_rom_folder(self) -> None:
        """Android drops iiSU's folder access when the VM is rebuilt, and that grant can
        only be given from inside iiSU, so the most this can do is get the VM started."""
        reply = QMessageBox.question(
            self, "Re-link your ROM folder",
            "Android doesn't keep iiSU's access to your ROM folder across a rebuild, and it can only "
            "be granted from inside iiSU.\n\nStart the VM now? Then, if iiSU asks for your ROM folder, "
            "pick it again.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.window.show_page("home")
            self.window.home_page._start()
