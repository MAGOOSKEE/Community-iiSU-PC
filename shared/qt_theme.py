"""
Shared dark-UI theme for Community-iiSU-PC's PySide6 front ends (replaces
shared/theme.py's tkinter/ttk version during the Qt rewrite -- see
C:\\Users\\Jaemin\\.claude\\plans\\robust-giggling-thompson.md).

Same palette and font roles as the tkinter theme (this is a brand carried
forward, not reinvented), but executed with what Qt actually has and Tk
didn't: real rounded corners, a native gradient brush instead of a strip of
1px canvas lines, and a stylesheet Qt fully owns end to end (including
dropdown popups, which stayed native/unstyled under ttk on Windows).

The palette echoes iiSU's own in-app look (dark background, tile-style
panels, its cyan-to-purple gradient) -- colors and layout only, not any of
iiSU's actual asset files (fonts, icons), since those are iiSU's own
copyrighted assets and not ours to include.
"""

from PySide6.QtGui import QColor, QFont, QLinearGradient, QPalette

BG = "#141414"
PANEL_BG = "#212124"
PANEL_BG_HOVER = "#2a2a2e"
TEXT = "#f2f2f4"
TEXT_DIM = "#9a9aa2"
GRADIENT_STOPS = ["#71e0ff", "#68ccff", "#5e84ff", "#8258fa", "#c56eff"]
GREEN = "#4fd67a"
RED = "#ff6161"
GRAY = "#5a5a60"

# Previously scattered as literals through bridge/manager.py under the Tk
# UI; centralized here now that the rewrite touches every call site anyway.
INPUT_BG = "#0e0e10"
LOG_TEXT = "#c9c9ce"
ACCENT_BG = "#3a3a40"
ACCENT_BG_HOVER = "#48484f"
DIRTY_BG = "#c98a2b"
DIRTY_BG_HOVER = "#d99a3b"
DIRTY_TEXT = "#1a1206"

# 8/16/24px spacing scale used consistently by PageBase/Card, rather than
# each page hand-tuning its own padding the way the Tk pages did.
SPACING_SM = 8
SPACING_MD = 16
SPACING_LG = 24

FONT_FAMILY_UI = "Segoe UI"
FONT_FAMILY_UI_SEMIBOLD = "Segoe UI Semibold"
FONT_FAMILY_MONO = "Consolas"


class Fonts:
    """Factory methods, not module-level QFont instances -- constructing a
    QFont before a QGuiApplication exists is unreliable, so these are only
    ever called after the app is up."""

    @staticmethod
    def title() -> QFont:
        return QFont(FONT_FAMILY_UI_SEMIBOLD, 20)

    @staticmethod
    def heading() -> QFont:
        return QFont(FONT_FAMILY_UI_SEMIBOLD, 11)

    @staticmethod
    def body() -> QFont:
        return QFont(FONT_FAMILY_UI, 10)

    @staticmethod
    def mono() -> QFont:
        return QFont(FONT_FAMILY_MONO, 9)


def gradient_brush(width: float, height: float = 0.0) -> QLinearGradient:
    """The cyan-to-purple accent gradient as a real QLinearGradient, for
    anything painted directly (GradientDivider, a card's accent edge, a
    hover glow) -- horizontal by default (height=0), matching every place
    this brand gradient is actually used today."""
    gradient = QLinearGradient(0, 0, width, height)
    n = len(GRADIENT_STOPS)
    for i, stop in enumerate(GRADIENT_STOPS):
        gradient.setColorAt(i / (n - 1), QColor(stop))
    return gradient


def build_palette() -> QPalette:
    """Base colors QPalette can express directly; anything QPalette can't
    (rounded corners, gradients, hover-state transitions, the dropdown
    popup) lives in STYLESHEET instead -- the two-layer approach avoids
    fighting Qt's style engine, rather than trying to force everything
    through one or the other."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(INPUT_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL_BG))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_DIM))
    palette.setColor(QPalette.ColorRole.Button, QColor(PANEL_BG))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(PANEL_BG))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(GRADIENT_STOPS[2]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#101010"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TEXT_DIM))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_DIM))
    return palette


# Object/dynamic-property names the QSS below keys off of, so page code
# reads "Card()" / button.setProperty("dirty", True) rather than sprinkling
# raw stylesheet strings through every page file:
#   QFrame#Card                              -- shared/widgets/card.py
#   QPushButton#accent / #ghost               -- primary / secondary actions
#   QPushButton[dirty="true"]                 -- amber "unsaved changes" Save button
STYLESHEET = f"""
QWidget {{
    color: {TEXT};
}}

QMainWindow, QDialog {{
    background-color: {BG};
}}

QToolTip {{
    background-color: {PANEL_BG};
    color: {TEXT};
    border: 1px solid {PANEL_BG_HOVER};
    padding: 4px 8px;
    border-radius: 4px;
}}

QFrame#Card {{
    background-color: {PANEL_BG};
    border-radius: 10px;
}}

QPushButton {{
    border: none;
    border-radius: 6px;
    padding: 6px 12px;
    background-color: {PANEL_BG};
    color: {TEXT};
}}
QPushButton:hover {{
    background-color: {PANEL_BG_HOVER};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
}}
QPushButton#accent {{
    padding: 10px 16px;
    background-color: {ACCENT_BG};
    color: {TEXT};
}}
QPushButton#accent:hover {{
    background-color: {ACCENT_BG_HOVER};
}}
QPushButton#accent:disabled {{
    background-color: {PANEL_BG_HOVER};
    color: {TEXT_DIM};
}}
QPushButton#ghost {{
    background-color: {PANEL_BG};
    color: {TEXT};
    padding: 6px 12px;
}}
QPushButton#ghost:hover {{
    background-color: {PANEL_BG_HOVER};
}}
QPushButton#ghost:disabled {{
    color: {TEXT_DIM};
}}
QPushButton[dirty="true"] {{
    background-color: {DIRTY_BG};
    color: {DIRTY_TEXT};
}}
QPushButton[dirty="true"]:hover {{
    background-color: {DIRTY_BG_HOVER};
}}

QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {INPUT_BG};
    color: {TEXT};
    border: 1px solid {PANEL_BG_HOVER};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {GRADIENT_STOPS[2]};
    selection-color: #101010;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {GRADIENT_STOPS[2]};
}}

QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid {PANEL_BG_HOVER};
    background-color: {INPUT_BG};
}}
QCheckBox::indicator:checked {{
    background-color: {GRADIENT_STOPS[2]};
    border: 1px solid {GRADIENT_STOPS[2]};
}}

QComboBox {{
    background-color: {INPUT_BG};
    color: {TEXT};
    border: 1px solid {PANEL_BG_HOVER};
    border-radius: 4px;
    padding: 4px 8px;
}}
QComboBox:focus {{
    border: 1px solid {GRADIENT_STOPS[2]};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {INPUT_BG};
    color: {TEXT};
    border: 1px solid {PANEL_BG_HOVER};
    selection-background-color: {GRADIENT_STOPS[2]};
    selection-color: #101010;
    outline: none;
}}

QProgressBar {{
    background-color: {PANEL_BG};
    border: none;
    border-radius: 4px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{
    background-color: {GRADIENT_STOPS[2]};
    border-radius: 4px;
}}

QTreeView, QTableView, QListView {{
    background-color: {INPUT_BG};
    alternate-background-color: {INPUT_BG};
    color: {TEXT};
    border: none;
    outline: none;
}}
QTreeView::item, QTableView::item, QListView::item {{
    padding: 4px;
}}
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {{
    background-color: {GRADIENT_STOPS[2]};
    color: #101010;
}}
QHeaderView::section {{
    background-color: {PANEL_BG};
    color: {TEXT_DIM};
    border: none;
    padding: 6px;
}}
QHeaderView::section:hover {{
    background-color: {PANEL_BG_HOVER};
}}

QScrollBar:vertical {{
    background: {BG};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {PANEL_BG_HOVER};
    min-height: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: {GRAY};
}}
QScrollBar:horizontal {{
    background: {BG};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {PANEL_BG_HOVER};
    min-width: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {GRAY};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}

QLabel[role="title"] {{
    color: {TEXT};
}}
QLabel[role="heading"] {{
    color: {TEXT};
}}
QLabel[role="dim"] {{
    color: {TEXT_DIM};
}}

QMenu {{
    background-color: {PANEL_BG};
    color: {TEXT};
    border: 1px solid {PANEL_BG_HOVER};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {GRADIENT_STOPS[2]};
    color: #101010;
}}
QMenu::separator {{
    height: 1px;
    background: {PANEL_BG_HOVER};
    margin: 4px 8px;
}}

QPushButton#NavButton {{
    background-color: transparent;
    border-radius: 0;
    text-align: left;
    padding: 10px 16px;
    font-size: 13px;
}}
QPushButton#NavButton:hover {{
    background-color: {PANEL_BG_HOVER};
}}
QPushButton#NavButton:checked {{
    background-color: {PANEL_BG_HOVER};
}}
QPushButton#NavButton:disabled {{
    color: {TEXT_DIM};
    background-color: transparent;
}}
QPushButton#DangerNavButton {{
    background-color: transparent;
    border-radius: 0;
    text-align: left;
    padding: 10px 16px;
    color: {RED};
    font-size: 13px;
}}
QPushButton#DangerNavButton:hover {{
    background-color: {PANEL_BG_HOVER};
}}

QPushButton#SubnavPill {{
    background-color: {PANEL_BG};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton#SubnavPill:hover {{
    background-color: {PANEL_BG_HOVER};
}}
QPushButton#SubnavPill:checked {{
    background-color: {PANEL_BG_HOVER};
}}
"""


def apply_theme(app) -> None:
    """Applies the palette, stylesheet, and default body font to a
    QApplication -- called once at each entry point's startup, mirroring
    the tkinter theme's apply_ttk_styles(style) call."""
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(STYLESHEET)
    app.setFont(Fonts.body())
