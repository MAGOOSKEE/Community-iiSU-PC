"""Add/edit a single Windows app mapping (executable, Steam game, or
custom URI), replaces manager.py's _windows_app_dialog() Toplevel.

Simplified from the original in one respect: Steam Game selection is a
"Choose from installed library..." button opening SteamLibraryDialog
rather than an inline live-search list with fetched box-art thumbnails,
same end result (an App ID gets filled in), without the extra async
image-fetching machinery. Can grow inline search back later if wanted."""

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QDialog,
)

from bridge.services import windows_apps_service as svc
from bridge.ui.dialogs.steam_library_dialog import SteamLibraryDialog
from shared.qt_theme import SPACING_MD, SPACING_SM

_LAUNCH_TYPES = ["Executable", "Steam Game", "Custom URI"]


class WindowsAppDialog(QDialog):
    def __init__(self, parent=None, title: str = "", initial_name: str = "", initial: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        initial = initial or {}
        self.result_values: tuple[str, dict] | None = None

        stored_type = str(initial.get("type", "executable")).lower()
        stored_uri = str(initial.get("uri", ""))
        if stored_type == "uri" and svc.is_steam_uri(stored_uri):
            initial_type = "Steam Game"
        elif stored_type == "uri":
            initial_type = "Custom URI"
        else:
            initial_type = "Executable"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        top = QGridLayout()
        top.addWidget(QLabel("Name:"), 0, 0)
        self.name_edit = QLineEdit(initial_name)
        top.addWidget(self.name_edit, 0, 1)
        top.addWidget(QLabel("Launch type:"), 1, 0)
        self.type_combo = QComboBox()
        self.type_combo.addItems(_LAUNCH_TYPES)
        self.type_combo.setCurrentText(initial_type)
        top.addWidget(self.type_combo, 1, 1)
        layout.addLayout(top)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # == Executable page ==
        exe_page = QWidget()
        exe_layout = QGridLayout(exe_page)
        exe_layout.addWidget(QLabel("Executable:"), 0, 0)
        self.exe_edit = QLineEdit(str(initial.get("exe", "")))
        exe_layout.addWidget(self.exe_edit, 0, 1)
        exe_browse = QPushButton("Browse...")
        exe_browse.setObjectName("ghost")
        exe_browse.clicked.connect(self._browse_exe)
        exe_layout.addWidget(exe_browse, 0, 2)
        exe_layout.addWidget(QLabel("Arguments:"), 1, 0)
        args = initial.get("args", [])
        self.args_edit = QLineEdit(" ".join(str(a) for a in args) if isinstance(args, list) else "")
        exe_layout.addWidget(self.args_edit, 1, 1, 1, 2)
        exe_layout.addWidget(QLabel("Working dir:"), 2, 0)
        self.work_edit = QLineEdit(str(initial.get("working_dir", "")))
        exe_layout.addWidget(self.work_edit, 2, 1)
        work_browse = QPushButton("Browse...")
        work_browse.setObjectName("ghost")
        work_browse.clicked.connect(self._browse_work)
        exe_layout.addWidget(work_browse, 2, 2)
        self.stack.addWidget(exe_page)

        # == Steam Game page ==
        steam_page = QWidget()
        steam_layout = QGridLayout(steam_page)
        steam_layout.addWidget(QLabel("Steam App ID:"), 0, 0)
        initial_appid = svc.steam_app_id(stored_uri) if initial_type == "Steam Game" else None
        self.steam_appid_edit = QLineEdit(initial_appid or "")
        steam_layout.addWidget(self.steam_appid_edit, 0, 1)
        pick_button = QPushButton("Choose from installed library...")
        pick_button.setObjectName("ghost")
        pick_button.clicked.connect(self._pick_steam_game)
        steam_layout.addWidget(pick_button, 1, 0, 1, 2)
        self.stack.addWidget(steam_page)

        # == Custom URI page ==
        uri_page = QWidget()
        uri_layout = QGridLayout(uri_page)
        uri_layout.addWidget(QLabel("URI:"), 0, 0)
        self.uri_edit = QLineEdit(stored_uri if initial_type == "Custom URI" else "")
        uri_layout.addWidget(self.uri_edit, 0, 1)
        self.stack.addWidget(uri_page)

        self.type_combo.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.stack.setCurrentIndex(_LAUNCH_TYPES.index(initial_type))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_edit.setFocus()

    def _browse_exe(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Select Windows application", filter="Windows applications (*.exe);;All files (*.*)"
        )
        if path:
            self.exe_edit.setText(path)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(path).stem)

    def _browse_work(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select working directory")
        if path:
            self.work_edit.setText(path)

    def _pick_steam_game(self) -> None:
        dialog = SteamLibraryDialog(self, multi_select=False)
        dialog.exec()
        if dialog.chosen:
            game_name, appid = dialog.chosen[0]
            self.steam_appid_edit.setText(appid)
            if not self.name_edit.text().strip() or self.name_edit.text().strip() == "":
                self.name_edit.setText(svc.safe_steam_pcgame_name(game_name))

    def _on_accept(self) -> None:
        name = self.name_edit.text().strip()
        launch_type = self.type_combo.currentText()
        if launch_type == "Executable":
            entry = {
                "type": "executable",
                "exe": self.exe_edit.text().strip(),
                "args": self.args_edit.text().split(),
                "working_dir": self.work_edit.text().strip(),
            }
        elif launch_type == "Steam Game":
            appid = self.steam_appid_edit.text().strip()
            entry = {"type": "uri", "uri": f"steam://rungameid/{appid}"} if appid else {"type": "uri", "uri": ""}
        else:
            entry = {"type": "uri", "uri": self.uri_edit.text().strip()}
        self.result_values = (name, entry)
        self.accept()
