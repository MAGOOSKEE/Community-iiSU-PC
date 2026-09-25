"""Material Icons as real QIcons, replaces the sidebar's first two
attempts (Segoe MDL2 line-art, then emoji): a bundled icon font renders as
solid, filled glyphs with real detail (Material Icons' "filled" style is
the one downloaded below) rather than a plain OS-supplied Unicode symbol,
which is what most polished desktop apps actually use.

shared/assets/fonts/MaterialIcons-Regular.ttf comes from Google's
material-design-icons repository (Apache License 2.0, LICENSE file
alongside it), each glyph is reached by its ASCII ligature name (e.g.
the text "home" renders as the home glyph) rather than a codepoint lookup,
which is the officially documented way to use this specific font.

Rendered to a QIcon (not embedded as button text) so it composes with
QPushButton's native icon+text layout, icon and label can use their own
fonts/colors independently, and collapsing the sidebar to icon-only mode
is just clearing the button's text, not string surgery.
"""

import functools
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap

_FONT_PATH = Path(__file__).resolve().parent.parent.parent / "shared" / "assets" / "fonts" / "MaterialIcons-Regular.ttf"
_family: str | None = None


def _family_name() -> str:
    global _family
    if _family is None:
        font_id = QFontDatabase.addApplicationFont(str(_FONT_PATH))
        families = QFontDatabase.applicationFontFamilies(font_id)
        _family = families[0] if families else "Material Icons"
    return _family


@functools.lru_cache(maxsize=None)
def icon(ligature: str, color: str, size: int = 22) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont(_family_name())
    font.setPixelSize(round(size * 0.82))
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, ligature)
    painter.end()
    return QIcon(pixmap)
