"""Common page scaffolding: the title/subtitle/gradient-divider header every
page repeats, plus the one layout rule worth baking in from day one.

That rule: wrapping a page that contains a wide, self-scrolling list (a
table/tree) inside an outer vertical-scroll wrapper stretches the whole
window instead of scrolling, and produces doubled scrollbars. QScrollArea
has that failure mode around a QTableView/QTreeView. So: a page containing a table/
tree/list gets that widget's size policy set to Expanding and placed
directly in the page's own top-level layout, never nested inside a
QScrollArea. Only a page with no internally-scrolling widget should pass
scrollable_body=True. This is a per-page, explicit choice (the constructor
argument below), not a default either way.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from bridge.ui.widgets.gradient_divider import GradientDivider
from shared.qt_theme import Fonts, SPACING_LG, SPACING_MD, TEXT, TEXT_DIM


class PageBase(QWidget):
    def __init__(self, parent=None, scrollable_body: bool = False):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if scrollable_body:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            body_host = QWidget()
            self.body_layout = QVBoxLayout(body_host)
            scroll.setWidget(body_host)
            outer.addWidget(scroll)
        else:
            self.body_layout = outer

        self.body_layout.setContentsMargins(SPACING_LG, 20, SPACING_LG, 20)
        self.body_layout.setSpacing(SPACING_MD)

    def add_header(self, title: str, subtitle: str = "") -> None:
        title_row = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setFont(Fonts.title())
        title_label.setStyleSheet(f"color: {TEXT};")
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        # Shown once the window gives this page a docs topic (set_help_topic).
        self._help_button = QPushButton("?")
        self._help_button.setObjectName("HelpButton")
        self._help_button.setFixedSize(30, 30)
        self._help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._help_button.setAccessibleName(f"Help for {title}")
        self._help_button.setVisible(False)
        title_row.addWidget(self._help_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body_layout.addLayout(title_row)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setFont(Fonts.body())
            subtitle_label.setStyleSheet(f"color: {TEXT_DIM};")
            subtitle_label.setWordWrap(True)

            self.body_layout.addWidget(subtitle_label)

        self.body_layout.addWidget(GradientDivider())

    def set_help_topic(self, topic: str) -> None:
        """Shows the "?" by the title, opening this page's section of the
        guides (see bridge/ui/help_links.py)."""
        button = getattr(self, "_help_button", None)
        if button is None:
            return
        from bridge.ui import help_links

        button.setToolTip(f"Open the guide for this page ({help_links.url(topic)})")
        button.clicked.connect(lambda: help_links.open_topic(topic))
        button.setVisible(True)

