"""A vertical list of a long job's steps, each ticked off as it finishes:
a check for done, a filled dot for the one running (with how long it has
taken so far), an empty circle for what's still to come, and a red mark on
the step that failed.

Setup uses it so a first run that takes several minutes shows where it is
and what's left, instead of a moving bar over a scrolling log.
"""

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from bridge.ui import icon_font
from shared.qt_theme import GREEN, GRADIENT_STOPS, RED, TEXT, TEXT_DIM

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"
_ICONS = {
    PENDING: ("radio_button_unchecked", TEXT_DIM),
    RUNNING: ("radio_button_checked", GRADIENT_STOPS[0]),
    DONE: ("check_circle", GREEN),
    FAILED: ("error", RED),
}


def format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds >= 60 else f"{seconds}s"


class StepChecklist(QWidget):
    def __init__(self, labels: list[str], hints: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self._hints = hints or {}
        self._rows: list[tuple[QLabel, QLabel, QLabel]] = []
        self._states: list[str] = []
        self._current = -1
        self._started_at = 0.0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(1, 1)
        self.set_labels(labels)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def set_labels(self, labels: list[str]) -> None:
        for icon, text, note in self._rows:
            for widget in (icon, text, note):
                widget.setParent(None)
        self._rows, self._states, self._current = [], [], -1
        for row, label in enumerate(labels):
            icon, text, note = QLabel(), QLabel(label), QLabel(self._hints.get(label, ""))
            note.setStyleSheet(f"color: {TEXT_DIM};")
            note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._grid.addWidget(icon, row, 0)
            self._grid.addWidget(text, row, 1)
            self._grid.addWidget(note, row, 2)
            self._rows.append((icon, text, note))
            self._states.append(PENDING)
            self._paint(row)

    def labels(self) -> list[str]:
        return [text.text() for _icon, text, _note in self._rows]

    def state(self, row: int) -> str:
        return self._states[row]

    def start(self, row: int) -> None:
        """Marks every step before row done and row as running."""
        for earlier in range(row):
            if self._states[earlier] != DONE:
                self._finish_row(earlier, DONE)
        self._current = row
        self._started_at = time.monotonic()
        self._states[row] = RUNNING
        self._paint(row)

    def finish_all(self) -> None:
        for row in range(len(self._rows)):
            if self._states[row] != DONE:
                self._finish_row(row, DONE)
        self._current = -1

    def fail_current(self) -> None:
        if self._current >= 0:
            self._finish_row(self._current, FAILED)
            self._current = -1

    def done_count(self) -> int:
        return sum(1 for state in self._states if state == DONE)

    def _finish_row(self, row: int, state: str) -> None:
        if row == self._current and self._started_at:
            self._rows[row][2].setText(format_elapsed(time.monotonic() - self._started_at))
        elif self._states[row] == PENDING:
            self._rows[row][2].setText("")
        self._states[row] = state
        self._paint(row)

    def _paint(self, row: int) -> None:
        icon, text, _note = self._rows[row]
        ligature, color = _ICONS[self._states[row]]
        icon.setPixmap(icon_font.icon(ligature, color, 18).pixmap(18, 18))
        text_color = TEXT_DIM if self._states[row] == PENDING else (RED if self._states[row] == FAILED else TEXT)
        weight = "600" if self._states[row] == RUNNING else "400"
        text.setStyleSheet(f"color: {text_color}; font-weight: {weight};")

    def _tick(self) -> None:
        if self._current < 0:
            return
        elapsed = format_elapsed(time.monotonic() - self._started_at)
        hint = self._hints.get(self.labels()[self._current], "")
        self._rows[self._current][2].setText(f"{hint}   {elapsed}" if hint else elapsed)
