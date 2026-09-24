"""Credits page -- ports manager.py's _build_credits_page and its avatar-
loading helpers. No internally-scrolling widget here (just stacked Cards),
so this is one of the pages that gets PageBase's scrollable_body=True."""

import webbrowser

import bridge.ui  # noqa: F401 -- import-time side effect: puts root/bridge/installer on sys.path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from bridge.ui.pages.base import PageBase
from bridge.ui.widgets.card import Card
from bridge.ui.workers.task_runner import run_in_background
from shared.qt_avatars import fetch_avatar_bytes, make_circular_pixmap, make_placeholder_circle
from shared.qt_theme import Fonts, GRADIENT_STOPS, TEXT, TEXT_DIM

_CONTRIBUTORS = [
    {
        "username": "Jacko1234wdd",
        "display_name": "Jacko1234wdd",
        "blurb": "Native Windows app/Steam launching, the Android Storage browser, and the Media Library/MediaBridge artwork pipeline.",
    },
]


class _AvatarLabel(QLabel):
    """A clickable circular avatar: shows a placeholder immediately, swaps
    in the real GitHub avatar once fetched (best-effort, degrades quietly
    on any failure -- see shared/qt_avatars.py)."""

    def __init__(self, username: str, display_name: str, size: int, tooltip: str = ""):
        super().__init__()
        self._username = username
        self._size = size
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setPixmap(make_placeholder_circle(size, display_name, GRADIENT_STOPS[2], "#101010"))
        if tooltip:
            self.setToolTip(tooltip)
        self._signals = run_in_background(fetch_avatar_bytes, self._apply, None, username)

    def _apply(self, data: bytes | None) -> None:
        if data is None:
            return
        pixmap = make_circular_pixmap(data, self._size)
        if pixmap is not None:
            self.setPixmap(pixmap)

    def mousePressEvent(self, event) -> None:
        webbrowser.open(f"https://github.com/{self._username}")


class CreditsPage(PageBase):
    def __init__(self, window, parent=None):
        super().__init__(parent, scrollable_body=True)
        self.window = window
        self._avatar_refs: list[_AvatarLabel] = []

        self.add_header("Credits", "Who made this, and how.")

        self._add_credit_row("MAGOOSKEE", "MAGOOSKEE", "Project owner -- built and maintains Community-iiSU-PC.")
        self._add_credit_row(
            "claude", "Claude (Anthropic)",
            "AI coding assistant -- wrote and refactored most of this codebase, including this Manager app, "
            "in collaboration with MAGOOSKEE.",
        )
        self._add_contributors_section()

        disclaimer = Card()
        disclaimer_layout = QVBoxLayout(disclaimer)
        disclaimer_label = QLabel(
            "AI disclosure: a large share of this project's code (including this Manager\n"
            "app) was written by Claude, an AI assistant, working under MAGOOSKEE's direction\n"
            "and review. If you're evaluating this project's safety or correctness, keep that\n"
            "in mind -- read the source rather than assuming a human wrote every line."
        )
        disclaimer_label.setStyleSheet(f"color: {TEXT_DIM};")
        disclaimer_layout.addWidget(disclaimer_label)
        self.body_layout.addWidget(disclaimer)
        self.body_layout.addStretch(1)

    def _add_credit_row(self, username: str, display_name: str, role: str) -> None:
        row = Card()
        row_layout = QHBoxLayout(row)

        avatar = _AvatarLabel(username, display_name, 64)
        self._avatar_refs.append(avatar)
        row_layout.addWidget(avatar)

        text_col = QWidget()
        text_layout = QVBoxLayout(text_col)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        name_label = QLabel(f'<a href="https://github.com/{username}" style="color:{GRADIENT_STOPS[2]};text-decoration:none;">{display_name}</a>')
        name_label.setFont(Fonts.heading())
        name_label.setOpenExternalLinks(True)
        text_layout.addWidget(name_label)

        github_label = QLabel(f"github.com/{username}")
        github_label.setStyleSheet(f"color: {TEXT_DIM};")
        text_layout.addWidget(github_label)

        role_label = QLabel(role)
        role_label.setWordWrap(True)
        role_label.setStyleSheet(f"color: {TEXT};")
        text_layout.addWidget(role_label)

        row_layout.addWidget(text_col, 1)
        self.body_layout.addWidget(row)

    def _add_contributors_section(self) -> None:
        if not _CONTRIBUTORS:
            return
        card = Card()
        card_layout = QVBoxLayout(card)

        heading = QLabel("Contributors")
        heading.setFont(Fonts.heading())
        card_layout.addWidget(heading)
        note = QLabel("Hover for what they worked on, click for their GitHub profile.")
        note.setStyleSheet(f"color: {TEXT_DIM};")
        card_layout.addWidget(note)

        avatar_row = QWidget()
        avatar_row_layout = QHBoxLayout(avatar_row)
        avatar_row_layout.setContentsMargins(0, 8, 0, 0)
        for person in _CONTRIBUTORS:
            username = person["username"]
            display_name = person.get("display_name", username)
            blurb = person.get("blurb", "")
            tooltip = f"{display_name}\n{blurb}" if blurb else display_name
            avatar = _AvatarLabel(username, display_name, 36, tooltip)
            self._avatar_refs.append(avatar)
            avatar_row_layout.addWidget(avatar)
        avatar_row_layout.addStretch(1)
        card_layout.addWidget(avatar_row)

        self.body_layout.addWidget(card)
