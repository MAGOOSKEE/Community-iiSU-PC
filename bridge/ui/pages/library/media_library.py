"""Media Library page -- ports manager.py's _build_media_library_page and
its core Check/Restore/preview flow. Backed by
bridge/services/media_library_service.py and the native soundbite player
in bridge/ui/audio/winmm_playback.py.

Deliberately not carried over in this pass: the iiDB Browser (a whole
separate search/cart/install sub-window, ~1300 lines in the original) --
"Browse iiDB" is present but reports it isn't ported yet rather than being
silently missing. Everything that manages ALREADY-saved media (the actual
point of this page day to day: Check/Restore/preview) is real.

Connection status is checked when this page becomes visible rather than
on Home's continuous 2-second poll regardless of which page is showing --
a deliberate simplification, and arguably better (no ADB traffic for a
page nobody's looking at).
"""

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bridge.services import android_storage_service
from bridge.services import media_library_service as svc
from bridge.ui.audio.winmm_playback import WinmmPlaybackError, WinmmPlayer, format_ms
from bridge.ui.dialogs.iidb_browser_dialog import IidbBrowserDialog
from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.widgets.status_dot import StatusDot
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_theme import Fonts, INPUT_BG, TEXT_DIM

_ASSET_ROLE = Qt.ItemDataRole.UserRole


class MediaLibraryPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=False)
        self.window = window
        self._tree_assets: dict[str, tuple[dict, dict]] = {}
        self._selected: tuple[dict, dict] | None = None
        self._player = WinmmPlayer()
        self._iidb_window: IidbBrowserDialog | None = None

        self.add_header("Media Library", "Durable iiDB artwork history and one-click recovery after iiSU rescans.")

        connection_row = QWidget()
        connection_layout = QHBoxLayout(connection_row)
        connection_layout.setContentsMargins(0, 0, 0, 0)
        self.connection_dot = StatusDot()
        connection_layout.addWidget(self.connection_dot)
        self.connection_label = QLabel("Checking VM connection...")
        self.connection_label.setStyleSheet(f"color: {TEXT_DIM};")
        connection_layout.addWidget(self.connection_label)
        connection_layout.addStretch(1)
        self.body_layout.addWidget(connection_row)

        summary_card = Card()
        summary_layout = QVBoxLayout(summary_card)
        self.summary_label = QLabel("Loading installed media registry...")
        self.summary_label.setFont(Fonts.heading())
        summary_layout.addWidget(self.summary_label)
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {TEXT_DIM};")
        summary_layout.addWidget(self.detail_label)
        self.body_layout.addWidget(summary_card)

        button_row = QWidget()
        button_row_layout = QHBoxLayout(button_row)
        button_row_layout.setContentsMargins(0, 0, 0, 0)
        check_button = QPushButton("Check iiSU Media")
        check_button.setObjectName("ghost")
        check_button.clicked.connect(self._check_media)
        button_row_layout.addWidget(check_button)
        restore_missing_button = QPushButton("Restore Missing")
        restore_missing_button.setObjectName("accent")
        restore_missing_button.clicked.connect(lambda: self._restore(False))
        button_row_layout.addWidget(restore_missing_button)
        restore_all_button = QPushButton("Restore All")
        restore_all_button.setObjectName("ghost")
        restore_all_button.clicked.connect(lambda: self._restore(True))
        button_row_layout.addWidget(restore_all_button)
        open_local_button = QPushButton("Open Local Library")
        open_local_button.setObjectName("ghost")
        open_local_button.clicked.connect(self._open_local_library)
        button_row_layout.addWidget(open_local_button)
        button_row_layout.addStretch(1)
        browse_iidb_button = QPushButton("Browse iiDB")
        browse_iidb_button.setObjectName("accent")
        browse_iidb_button.clicked.connect(self._open_iidb_browser)
        button_row_layout.addWidget(browse_iidb_button)
        self.body_layout.addWidget(button_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_layout.addWidget(splitter, 1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Game / Asset", "Slot", "Status", "Local file"])
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.tree)

        preview_panel = Card()
        preview_layout = QVBoxLayout(preview_panel)
        preview_heading = QLabel("Asset Viewer")
        preview_heading.setFont(Fonts.heading())
        preview_layout.addWidget(preview_heading)

        self.preview_label = QLabel("Select an asset\nto preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(f"color: {TEXT_DIM}; background-color: {INPUT_BG};")
        self.preview_label.setMinimumHeight(220)
        preview_layout.addWidget(self.preview_label, 1)

        self.preview_detail_label = QLabel("")
        self.preview_detail_label.setWordWrap(True)
        self.preview_detail_label.setStyleSheet(f"color: {TEXT_DIM};")
        preview_layout.addWidget(self.preview_detail_label)

        self.audio_widget = QWidget()
        audio_layout = QHBoxLayout(self.audio_widget)
        audio_layout.setContentsMargins(0, 0, 0, 0)
        self.play_button = QPushButton("▶")
        self.play_button.setFixedWidth(36)
        self.play_button.clicked.connect(self._toggle_audio)
        audio_layout.addWidget(self.play_button)
        self.time_label = QLabel("0:00 / 0:00")
        audio_layout.addWidget(self.time_label)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        audio_layout.addWidget(self.seek_slider, 1)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 1000)
        self.volume_slider.setValue(850)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.valueChanged.connect(self._player.set_volume)
        audio_layout.addWidget(self.volume_slider)
        preview_layout.addWidget(self.audio_widget)
        self.audio_widget.hide()

        self.open_file_button = QPushButton("Open File")
        self.open_file_button.setObjectName("ghost")
        self.open_file_button.clicked.connect(self._open_selected_file)
        preview_layout.addWidget(self.open_file_button)

        splitter.addWidget(preview_panel)
        splitter.setSizes([500, 260])

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(100)
        self._tick_timer.timeout.connect(self._audio_tick)

        self._refresh_signals = None
        self._check_signals = None
        self._restore_signals = None
        self._connection_signals = None

    def on_shown(self) -> None:
        self.refresh()
        self._check_connection()

    # -- Registry summary + tree -------------------------------------------------

    def refresh(self) -> None:
        try:
            registry = svc.load_media_registry()
            rows = list(svc.media_records(registry))
        except svc.MediaLibraryServiceError as e:
            self.summary_label.setText("Installed media registry error")
            self.detail_label.setText(str(e))
            return

        games = len({key for key, _game, _asset in rows})
        total_bytes = 0
        for _key, _game, asset in rows:
            try:
                total_bytes += svc.media_local_file(asset).stat().st_size
            except OSError:
                pass

        self._populate_tree(rows)
        self.summary_label.setText(
            f"{games} game{'s' if games != 1 else ''} • {len(rows)} saved asset{'s' if len(rows) != 1 else ''} "
            f"• {total_bytes / (1024 * 1024):.1f} MB"
        )
        self.detail_label.setText('Stored locally -- use "Open Local Library" below to browse the actual folder.')

    def _populate_tree(self, rows, checked: bool = False) -> None:
        """rows: (key, game, asset) or (key, game, asset, status, info)."""
        self.tree.clear()
        self._tree_assets = {}
        grouped: dict[str, dict] = {}
        for row in rows:
            key, game, asset = row[:3]
            status = row[3] if len(row) >= 4 else "Saved"
            grouped.setdefault(key, {"game": game, "items": []})["items"].append((asset, status))

        for key, bucket in grouped.items():
            game = bucket["game"]
            items = bucket["items"]
            name = game.get("display_name", "Unknown")
            bad = sum(1 for _asset, status in items if status not in {"OK", "Saved"})
            overall = ("All OK" if bad == 0 else f"{bad} need attention") if checked else "Saved"
            count = len(items)
            parent = QTreeWidgetItem([name, "", f"{count} asset{'s' if count != 1 else ''} • {overall}", ""])
            self.tree.addTopLevelItem(parent)
            for index, (asset, status) in enumerate(items):
                label = str(asset.get("asset_type", "?")).replace("_", " ").title()
                display_status = str(status).replace("_", " ").title()
                child = QTreeWidgetItem([label, str(asset.get("slot", 1)), display_status, asset.get("file", "")])
                asset_key = f"{key}-{index}"
                child.setData(0, _ASSET_ROLE, asset_key)
                parent.addChild(child)
                self._tree_assets[asset_key] = (game, asset)
        self.tree.expandAll()

    # -- Selection / preview -------------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            self._clear_preview()
            return
        record = self._tree_assets.get(items[0].data(0, _ASSET_ROLE))
        if record is None:
            self._clear_preview("Select one of this game's\nassets to preview")
            return

        self._stop_audio()
        game, asset = record
        self._selected = record
        local = svc.media_local_file(asset)
        asset_type = str(asset.get("asset_type", "?"))
        slot = int(asset.get("slot", 1))
        detail = [str(game.get("display_name", "Unknown")), f"{asset_type.replace('_', ' ').title()} • Slot {slot}", local.name]
        try:
            detail.append(svc.human_size(local.stat().st_size))
        except OSError:
            pass
        self.preview_detail_label.setText("\n".join(x for x in detail if x))
        self.audio_widget.hide()
        self.preview_label.setPixmap(QPixmap())

        if not local.is_file():
            self.preview_label.setText("Local library file\nis missing")
            return

        if asset_type == "soundbite":
            self.preview_label.setText("♪\nSoundbite")
            self.time_label.setText("0:00 / 0:00")
            self.play_button.setText("▶")
            self.seek_slider.setValue(0)
            self.audio_widget.show()
            return

        pixmap = QPixmap(str(local))
        if pixmap.isNull():
            self.preview_label.setText("Preview unavailable")
            return
        scaled = pixmap.scaled(280, 380, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview_label.setPixmap(scaled)
        self.preview_detail_label.setText(self.preview_detail_label.text() + f"\n{pixmap.width()}×{pixmap.height()}")

    def _clear_preview(self, text: str = "Select an asset\nto preview") -> None:
        self._stop_audio()
        self._selected = None
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(text)
        self.preview_detail_label.setText("")
        self.audio_widget.hide()

    def _open_selected_file(self) -> None:
        if not self._selected:
            return
        _game, asset = self._selected
        local = svc.media_local_file(asset)
        if not local.is_file():
            QMessageBox.warning(self, "Media Library", f"Local file not found:\n{local}")
            return
        import os

        try:
            os.startfile(str(local))
        except Exception as exc:
            QMessageBox.critical(self, "Media Library", f"Couldn't open:\n{local}\n\n{exc}")

    # -- Soundbite playback -------------------------------------------------

    def _toggle_audio(self) -> None:
        if not self._selected:
            return
        if self._player.state == "stopped" and not self._player.has_audio_loaded:
            _game, asset = self._selected
            local = svc.media_local_file(asset)
            if not local.is_file():
                QMessageBox.warning(self, "Media Library", f"Local soundbite not found:\n{local}")
                return
            self.play_button.setText("…")
            try:
                self._player.load_and_play(local)
            except Exception as exc:
                self._stop_audio()
                QMessageBox.critical(
                    self,
                    "Media Library",
                    "Soundbite preview failed.\n\nThe saved original is still intact and will continue to be used by iiSU.\n\n"
                    f"{exc}",
                )
                return
        else:
            try:
                self._player.toggle()
            except WinmmPlaybackError as exc:
                QMessageBox.critical(self, "Media Library", f"Soundbite preview failed.\n\n{exc}")
                return

        self.play_button.setText("‖" if self._player.state == "playing" else "▶")
        if self._player.state == "playing":
            self._tick_timer.start()
        else:
            self._tick_timer.stop()

    def _audio_tick(self) -> None:
        if self._player.state not in ("playing", "paused"):
            self._tick_timer.stop()
            return
        position = self._player.position_ms()
        length = self._player.length_ms
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(int(position * 1000 / length) if length else 0)
        self.seek_slider.blockSignals(False)
        self.time_label.setText(f"{format_ms(position)} / {format_ms(length)}")
        if length and position >= length:
            self._player.mark_ended()
            self.play_button.setText("▶")
            self.seek_slider.setValue(0)
            self.time_label.setText(f"0:00 / {format_ms(length)}")
            self._tick_timer.stop()

    def _on_seek_released(self) -> None:
        length = self._player.length_ms
        if not length:
            return
        target = int(self.seek_slider.value() * length / 1000)
        try:
            self._player.seek(target)
        except WinmmPlaybackError as exc:
            QMessageBox.critical(self, "Media Library", f"Couldn't seek soundbite.\n\n{exc}")
            return
        self.play_button.setText("‖" if self._player.state == "playing" else "▶")
        if self._player.state == "playing":
            self._tick_timer.start()

    def _stop_audio(self) -> None:
        self._tick_timer.stop()
        self._player.close()
        self.play_button.setText("▶")
        self.time_label.setText("0:00 / 0:00")
        self.seek_slider.setValue(0)

    # -- Connection indicator -------------------------------------------------

    def _check_connection(self) -> None:
        self.connection_dot.set_state("unknown")
        self.connection_label.setText("Checking VM connection...")
        self._connection_signals = run_in_background(self._check_connection_worker, self._apply_connection)

    def _check_connection_worker(self):
        ready, _detail = android_storage_service.adb_device_ready()
        if not ready:
            return "down", "VM Disconnected"
        ok, _detail = svc.mediabridge_ping()
        return ("up", "VM Connected • MediaBridge Ready") if ok else ("down", "VM Connected • MediaBridge Unavailable")

    def _apply_connection(self, result) -> None:
        state, text = result
        self.connection_dot.set_state(state)
        self.connection_label.setText(text)

    # -- Actions -------------------------------------------------

    def _open_local_library(self) -> None:
        import os

        svc.IIDB_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(svc.IIDB_LIBRARY_DIR))

    def _check_media(self) -> None:
        ready, detail = android_storage_service.adb_device_ready()
        if not ready:
            QMessageBox.warning(self, "Media Library", "Start the Android VM before checking iiSU media.\n\n" + detail)
            return
        self.summary_label.setText("Checking iiSU media...")
        self._check_signals = run_in_background(self._check_media_worker, self._apply_check_results, None)

    def _check_media_worker(self):
        registry = svc.load_media_registry()
        rows = []
        for key, game, asset in svc.media_records(registry):
            status, info = svc.media_check_asset(game, asset)
            rows.append((key, game, asset, status, info))
        return rows

    def _apply_check_results(self, rows) -> None:
        missing = sum(1 for *_rest, status, _info in rows if status != "OK")
        self._populate_tree(rows, checked=True)
        self.summary_label.setText(f"Check complete • {len(rows) - missing} correct • {missing} need attention")

    def _restore(self, restore_all: bool) -> None:
        ready, detail = android_storage_service.adb_device_ready()
        if not ready:
            QMessageBox.warning(self, "Media Library", "Start the Android VM before restoring media.\n\n" + detail)
            return
        ping_ok, ping_detail = svc.mediabridge_ping()
        if not ping_ok:
            QMessageBox.critical(self, "Media Library", "MediaBridge V1 is not ready. Repatch iiSU first.\n\n" + ping_detail)
            return
        if restore_all:
            reply = QMessageBox.question(
                self,
                "Restore All Media",
                "Reinstall every saved media asset through MediaBridge?\n\n"
                "This intentionally replaces the corresponding iiSU media slots with the saved copies.",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.summary_label.setText("Preparing restore...")
        self._restore_signals = run_in_background(self._restore_worker, self._apply_restore_result, None, restore_all)

    def _restore_worker(self, restore_all: bool):
        import re as re_module

        registry = svc.load_media_registry()
        restored = skipped = failed = 0
        failures = []
        for _key, game, asset in svc.media_records(registry):
            local = svc.media_local_file(asset)
            if not local.is_file():
                failed += 1
                failures.append(f"{game.get('display_name')}: local copy missing")
                continue
            if not restore_all:
                status, _info = svc.media_check_asset(game, asset)
                if status == "OK":
                    skipped += 1
                    continue
                if status.startswith("LOCAL_"):
                    failed += 1
                    failures.append(f"{game.get('display_name')}: {status}")
                    continue
            ok, output = svc.mediabridge_install_file(game, asset, local)
            if ok:
                restored += 1
            else:
                failed += 1
                match = re_module.search(r"IISUPC_MEDIABRIDGE_ERROR_V1:([A-Z0-9_]+)", output or "")
                detail = f"MediaBridge: {match.group(1)}" if match else (output or "Unknown restore error")
                failures.append(f"{game.get('display_name')} {asset.get('asset_type')}: {detail}")
        return restored, skipped, failed, failures

    def _apply_restore_result(self, result) -> None:
        restored, skipped, failed, failures = result
        self.refresh()
        self.summary_label.setText(f"Restore complete • {restored} restored • {skipped} already correct • {failed} failed")
        if failures:
            QMessageBox.warning(self, "Media Restore", "Some assets could not be restored:\n\n" + "\n".join(failures[:10]))
        else:
            QMessageBox.information(self, "Media Restore", f"Restore complete.\n\nRestored: {restored}\nAlready correct: {skipped}")

    def _open_iidb_browser(self) -> None:
        if self._iidb_window is not None:
            self._iidb_window.show()
            self._iidb_window.raise_()
            self._iidb_window.activateWindow()
            return
        window = IidbBrowserDialog(self)
        window.finished.connect(self._on_iidb_browser_closed)
        window.show()
        self._iidb_window = window

    def _on_iidb_browser_closed(self, _result=None) -> None:
        self._iidb_window = None
        self.refresh()
