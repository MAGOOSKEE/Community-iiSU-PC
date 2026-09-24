"""Left navigation rail -- replaces manager.py's hand-built tk.Label nav
buttons (_build_sidebar/_add_nav_button) with real QPushButtons, which get
checked-state styling, keyboard focus, and hover for free instead of the
three bind()s per button the Tk version needed.

NAV_ITEMS/NAV_GROUPS/DANGER_NAV_ITEMS are the same pure data manager.py
already had -- only the widgets reading them changed toolkit."""

from PySide6.QtCore import QSize, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from bridge.ui.icon_font import icon
from shared.qt_theme import FONT_FAMILY_UI, Fonts, PANEL_BG, PANEL_BG_HOVER, RED, TEXT

SIDEBAR_WIDTH_EXPANDED = 200
SIDEBAR_WIDTH_COLLAPSED = 56
_ICON_SIZE = 20


def _no_mnemonic(text: str) -> str:
    """QPushButton/QRadioButton/etc. treat a lone '&' as a mnemonic marker
    (it vanishes and underlines the next character) -- doubling it is Qt's
    own escape for a literal ampersand, needed for labels like "Backup &
    Diagnostics" that would otherwise render as "Backup _Diagnostics"."""
    return text.replace("&", "&&")

# Material Icons ligature names -- see bridge/ui/icon_font.py's docstring
# for why a name string rather than a codepoint.
NAV_ITEMS = [
    ("home", "home", "Home"),
    ("library", "video_library", "Library"),
    ("games", "sports_esports", "Games"),
    ("emulators", "desktop_windows", "Emulators"),
    ("settings", "settings", "Settings"),
    ("backup_diagnostics", "build", "Backup & Diagnostics"),
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
        ("windows_apps", "PC"),
    ],
    "emulators": [
        ("emulators", "PC Emulators"),
    ],
    "settings": [
        ("settings", "Display"),
        ("advanced", "Advanced"),
    ],
    "backup_diagnostics": [
        ("backup_restore", "Backup & Restore"),
        ("diagnostics", "Diagnostics"),
    ],
}

# Top-level nav entries that need a real install before they're useful.
LOCKED_NAV = {"library", "games", "emulators", "settings", "backup_diagnostics"}


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
        root.setContentsMargins(0, 16, 0, 12)
        root.setSpacing(0)

        header = QPushButton(" Community-iiSU-PC")
        header.setObjectName("NavButton")
        header.setFont(Fonts.heading())
        header.setFlat(True)
        header.setIcon(icon("menu", TEXT, _ICON_SIZE))
        header.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        header.setStyleSheet("text-align: left; padding: 4px 16px 14px 16px; font-weight: 600;")
        header.clicked.connect(self.toggle_clicked.emit)
        self._header_button = header
        root.addWidget(header)

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

    def _make_nav_button(self, key: str, glyph: str, label: str, danger: bool = False) -> QPushButton:
        btn = QPushButton(f" {_no_mnemonic(label)}")
        btn.setObjectName("DangerNavButton" if danger else "NavButton")
        btn.setFont(QFont(FONT_FAMILY_UI, 10))
        btn.setIcon(icon(glyph, RED if danger else TEXT, _ICON_SIZE))
        btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        btn.setCheckable(True)
        btn.setFlat(True)
        btn.clicked.connect(lambda: self.nav_clicked.emit(key))
        self.nav_buttons[key] = btn
        return btn

    def build_subnav(self, group_key: str, on_pill: QWidget) -> None:
        """Populates a caller-owned pill row for one group -- the pills
        themselves live in the page area (next to that group's content),
        not the sidebar rail, matching manager.py's segmented sub-nav."""
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
        self._header_button.setText(" Community-iiSU-PC" if expanded else "")
        for key, _glyph, label in NAV_ITEMS + DANGER_NAV_ITEMS:
            self.nav_buttons[key].setText(f" {_no_mnemonic(label)}" if expanded else "")
