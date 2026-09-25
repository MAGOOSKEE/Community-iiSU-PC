"""
Fetches a GitHub user's avatar and renders it as a circular QPixmap, for
the Credits page in bridge/ui (replaces shared/avatars.py's tkinter
version during the Qt rewrite).

GitHub serves a user's current avatar at a stable, unauthenticated URL,
https://github.com/<username>.png, which redirects to the real image on
avatars.githubusercontent.com. No API token or rate-limited REST call is
needed just to show a picture.

Everything here is best-effort: no internet or a GitHub outage degrades to
a plain colored circle with the user's first initial rather than break the
page, the same "cosmetic nice-to-have degrades quietly" approach
create_shortcut.py already takes for iiSU's own icon.

Unlike the tkinter version, this has no Pillow dependency at all: Qt's
QPixmap/QImage decode PNG/JPEG natively, and QPainter's antialiasing
already smooths a circular clip well at avatar sizes (64-96px) without
needing Pillow's manual supersample-then-downscale trick.
"""

import urllib.request

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPixmap

GITHUB_AVATAR_URL = "https://github.com/{username}.png"
FETCH_TIMEOUT = 5.0


def fetch_avatar_bytes(username: str) -> bytes | None:
    """Runs on a background thread (network I/O), callers shouldn't call
    this from the Qt main thread. Returns None on any failure at all."""
    try:
        req = urllib.request.Request(
            GITHUB_AVATAR_URL.format(username=username),
            headers={"User-Agent": "Community-iiSU-PC"},
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return resp.read()
    except Exception:
        return None


def make_circular_pixmap(image_bytes: bytes, size: int) -> QPixmap | None:
    """Converts raw image bytes into a circular QPixmap of (size x size).
    Returns None if the bytes aren't a decodable image, callers fall back
    to make_placeholder_circle in that case. Must be called from the Qt
    main thread (QPixmap needs a live QApplication)."""
    source = QPixmap()
    if not source.loadFromData(image_bytes):
        return None

    source = source.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
    )

    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size, size))
    painter.setClipPath(path)
    # Center-crop when the scaled source isn't already square (KeepAspectRatioByExpanding
    # only guarantees both dimensions are >= size, not that it's already square).
    x = (source.width() - size) // 2
    y = (source.height() - size) // 2
    painter.drawPixmap(-x, -y, source)
    painter.end()
    return result


def make_placeholder_circle(size: int, label: str, color: str, text_color: str) -> QPixmap:
    """A plain painted circle with a single letter, used whenever a real
    avatar couldn't be fetched or decoded, no network, cannot fail short
    of Qt itself being broken."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    painter.drawEllipse(0, 0, size, size)

    painter.setPen(QColor(text_color))
    font = QFont("Segoe UI Semibold", int(size * 0.4))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, (label[:1] or "?").upper())
    painter.end()
    return pixmap
