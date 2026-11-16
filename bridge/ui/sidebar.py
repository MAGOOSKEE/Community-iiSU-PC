"""Left navigation rail of real QPushButtons, which get checked-state
styling, keyboard focus, and hover for free.

NAV_ITEMS/NAV_GROUPS/DANGER_NAV_ITEMS are the pure data the widgets read."""

import bridge.ui  # noqa: F401; import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import updater
from bridge.ui.icon_font import icon
from bridge.ui.widgets.brand_mark import BrandMark
from shared.platform_compat import open_uri
from shared.qt_theme import Fonts, PANEL_BG, PANEL_BG_HOVER, RED, TEXT, TEXT_DIM

SIDEBAR_WIDTH_EXPANDED = 200
SIDEBAR_WIDTH_COLLAPSED = 56
_ICON_SIZE = 20
_HEADER_MARK_HEIGHT = 20
_HEADER_TITLE = "MANAGER"
_HEADER_TITLE_PIXEL_SIZE = 15
# Where the mark starts while the rail is open: past the menu icon.
_HEADER_MARK_INDENT = 16 + _ICON_SIZE + 14


def short_version(version: str) -> str:
    """The version without its pre-release suffix ("v0.6.0-alpha" becomes
    "v0.6.0"), which is all the collapsed rail has room for."""
    return version.split("-", 1)[0]


def release_url(version: str | None) -> str:
    """This version's release page on GitHub, or the list of releases for a
    build with no version."""
    releases = f"https://github.com/{updater.GITHUB_REPO}/releases"
    return f"{releases}/tag/{version}" if version else releases


class ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802; Qt's own name
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


def _no_mnemonic(text: str) -> str:
    """QPushButton/QRadioButton/etc. treat a lone '&' as a mnemonic marker
    (it vanishes and underlines the next character), doubling it is Qt's
    own escape for a literal ampersand, needed for labels like "Backup &
    Tools" that would otherwise render as "Backup _Tools"."""
    return text.replace("&", "&&")

# Material Icons ligature names, see bridge/ui/icon_font.py's docstring
# for why a name string rather than a codepoint.
NAV_ITEMS = [
    ("home", "home", "Home"),
    ("library", "video_library", "Library"),
    ("games", "sports_esports", "Games"),
    ("settings", "settings", "Settings"),
    ("backup_diagnostics", "build", "Backup & Tools"),
    ("updates", "system_update", "Updates"),
    ("credits", "emoji_events", "Credits"),
]
DANGER_NAV_ITEMS = [
    ("uninstall", "delete", "Uninstall"),
]

NAV_GROUPS: dict[str, list[tuple[str, str]]] = {
    "library": [
        ("roms", "ROM Directory"),
        ("media_library", "Media Library"),
        ("android_storage", "Android Storage"),
    ],
    "games": [
        ("games_console", "Console"),
        ("pc_games", "PC"),
    ],
    "settings": [
        ("general", "General"),
        ("settings", "Display"),
        ("controls", "Controls"),
        ("emulator", "VM Profiles"),
        ("emulators", "PC Emulators"),
        ("storage", "Storage"),
        ("advanced", "Advanced"),
    ],
    "backup_diagnostics": [
        ("backup_restore", "Backup & Restore"),
        ("diagnostics", "Diagnostics"),
    ],
}

# Top-level nav entries that need a real install before they're useful.
LOCKED_NAV = {"library", "games", "settings", "backup_diagnostics"}


class Sidebar(QWidget):
    nav_clicked = Signal(str)
    subnav_clicked = Signal(str, str)
    toggle_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(SIDEBAR_WIDTH_EXPANDED)
        self.setObjectName("Sidebar")
        self.setStyleSheet(f"#Sidebar {{ background-color: {PANEL_BG}; }}")
        self.setAutoFillBackground(True)

        self._expanded = True
        self.nav_buttons: dict[str, QPushButton] = {}
        self.subnav_buttons: dict[str, dict[str, QPushButton]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 12)
        root.setSpacing(0)

        # The iiPC mark and "MANAGER", not the name spelled out: they sit
        # inside the button, to the right of the menu icon, and let clicks
        # through. Collapsed, the mark alone is the button (see set_expanded).
        header = QPushButton("")
        header.setObjectName("NavButton")
        header.setFlat(True)
        header.setToolTip("Community-iiSU-PC Manager")
        header.setAccessibleName("Community-iiSU-PC Manager, collapse or expand the sidebar")
        self._menu_icon = icon("menu", TEXT, _ICON_SIZE)
        header.setIcon(self._menu_icon)
        header.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        # Even padding so the hover highlight sits evenly around the title. It was 4px
        # above and 14px below, which made the highlight look cut off at the top. The
        # gap before the first page lives outside the button instead. Scoped to the
        # button itself, or the padding would land on the title label inside it too.
        header.setStyleSheet("QPushButton { text-align: left; padding: 10px 16px; font-weight: 600; }")
        # The same height with the menu icon and without it.
        header.setMinimumHeight(_ICON_SIZE + 20)
        header.clicked.connect(self.toggle_clicked.emit)
        self._header_mark = BrandMark(_HEADER_MARK_HEIGHT)
        self._header_title = QLabel(_HEADER_TITLE)
        title_font = Fonts.ui(11, semibold=True)
        title_font.setPixelSize(_HEADER_TITLE_PIXEL_SIZE)
        self._header_title.setFont(title_font)
        # The padding above the letters is so that, centred beside the mark,
        # they stand on the same line as the bottom of "PC".
        self._header_title.setStyleSheet(f"color: {TEXT}; background: transparent; padding: 8px 0 0 0;")
        self._header_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self._header_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(_HEADER_MARK_INDENT, 0, 0, 0)
        header_layout.setSpacing(5)
        # Takes up the slack only while collapsed, to centre the mark.
        header_layout.addStretch(0)
        header_layout.addWidget(self._header_mark, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self._header_title, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addStretch(1)
        self._header_layout = header_layout
        self._header_button = header
        root.addWidget(header)
        root.addSpacing(4)

        for key, glyph, label in NAV_ITEMS:
            root.addWidget(self._make_nav_button(key, glyph, label))

        root.addStretch(1)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {PANEL_BG_HOVER};")
        root.addSpacing(8)
        root.addWidget(divider)
        root.addSpacing(8)

        for key, glyph, label in DANGER_NAV_ITEMS:
            root.addWidget(self._make_nav_button(key, glyph, label, danger=True))

        self._version = updater.get_installed_version() or "dev"
        self.version_label = ClickableLabel("")
        self.version_label.setFont(Fonts.ui(8))
        self._refresh_version_label()
        self.version_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_label.setToolTip("Open this version's release page")
        self.version_label.clicked.connect(lambda: open_uri(release_url(updater.get_installed_version())))
        root.addWidget(self.version_label)

    def _make_nav_button(self, key: str, glyph: str, label: str, danger: bool = False) -> QPushButton:
        btn = QPushButton(f" {_no_mnemonic(label)}")
        btn.setObjectName("DangerNavButton" if danger else "NavButton")
        btn.setFont(Fonts.ui(10))
        btn.setIcon(icon(glyph, RED if danger else TEXT, _ICON_SIZE))
        btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        btn.setCheckable(True)
        btn.setFlat(True)
        btn.clicked.connect(lambda: self.nav_clicked.emit(key))
        self.nav_buttons[key] = btn
        return btn

    def build_subnav(self, group_key: str, on_pill: QWidget) -> None:
        """Populates a caller-owned pill row for one group, the pills
        themselves live in the page area (next to that group's content),
        not the sidebar rail."""
        layout = on_pill.layout() or QHBoxLayout(on_pill)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.subnav_buttons[group_key] = {}
        for sub_key, label in NAV_GROUPS[group_key]:
            pill = QPushButton(_no_mnemonic(label))
            pill.setObjectName("SubnavPill")
            pill.setFont(Fonts.body())
            pill.setCheckable(True)
            pill.setFlat(True)
            pill.clicked.connect(lambda _checked=False, gk=group_key, sk=sub_key: self.subnav_clicked.emit(gk, sk))
            layout.addWidget(pill)
            self.subnav_buttons[group_key][sub_key] = pill
        layout.addStretch(1)

    def set_current(self, key: str) -> None:
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)

    def set_current_sub(self, group_key: str, sub_key: str) -> None:
        for k, btn in self.subnav_buttons.get(group_key, {}).items():
            btn.setChecked(k == sub_key)

    def set_nav_enabled(self, key: str, enabled: bool) -> None:
        self.nav_buttons[key].setEnabled(enabled)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.setFixedWidth(SIDEBAR_WIDTH_EXPANDED if expanded else SIDEBAR_WIDTH_COLLAPSED)
        # Collapsed, the mark takes the menu icon's place, centred in the
        # rail. With no mark to show, the menu icon stays.
        mark_only = not expanded and self._header_mark.is_available()
        self._header_button.setIcon(QIcon() if mark_only else self._menu_icon)
        self._header_layout.setContentsMargins(0 if mark_only else _HEADER_MARK_INDENT, 0, 0, 0)
        self._header_layout.setStretch(0, 1 if mark_only else 0)
        self._header_mark.setVisible(expanded or mark_only)
        self._header_title.setVisible(expanded)
        for key, _glyph, label in NAV_ITEMS + DANGER_NAV_ITEMS:
            self.nav_buttons[key].setText(f" {_no_mnemonic(label)}" if expanded else "")
        self._refresh_version_label()

    def _refresh_version_label(self) -> None:
        """The whole version while open, and just its number, centred, in
        the collapsed rail."""
        if self._expanded:
            self.version_label.setText(f" {self._version}")
            self.version_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.version_label.setStyleSheet(f"color: {TEXT_DIM}; padding: 8px 16px 0 16px;")
        else:
            self.version_label.setText(short_version(self._version))
            self.version_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.version_label.setStyleSheet(f"color: {TEXT_DIM}; padding: 8px 0 0 0;")
