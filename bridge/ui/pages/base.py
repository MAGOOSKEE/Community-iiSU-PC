"""Common page scaffolding: the title/subtitle/gradient-divider header every
page repeats, plus the one layout rule worth baking in from day one.

That rule, from a real Tk bug this project already hit once: wrapping a
page that contains a wide, self-scrolling list (a Treeview/table) inside an
outer vertical-scroll wrapper stretched the whole window instead of
scrolling, and produced doubled scrollbars. QScrollArea has the exact same
failure mode around a QTableView/QTreeView. So: a page containing a table/
tree/list gets that widget's size policy set to Expanding and placed
directly in the page's own top-level layout, never nested inside a
QScrollArea. Only a page with no internally-scrolling widget should pass
scrollable_body=True. This is a per-page, explicit choice (the constructor
argument below), not a default either way.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

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
        title_label = QLabel(title)
        title_label.setFont(Fonts.title())
        title_label.setStyleSheet(f"color: {TEXT};")
        self.body_layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setFont(Fonts.body())
            subtitle_label.setStyleSheet(f"color: {TEXT_DIM};")
            self.body_layout.addWidget(subtitle_label)

        self.body_layout.addWidget(GradientDivider())
