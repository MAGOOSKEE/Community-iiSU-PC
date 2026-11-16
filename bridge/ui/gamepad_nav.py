"""Drives the Manager with a controller, for a couch, a TV or a Steam Deck in
Desktop Mode, where reaching for a mouse is the awkward part.

  D-pad or left stick   move between controls (and through a list's rows)
  A                     press the focused button, tick the box, open the row
  B                     back: closes a dialog or menu
  LB / RB               previous / next page in the sidebar

It only acts while a Manager window is the active window, so a controller
playing a game, or driving iiSU, is never read here as Manager input.

GamepadNavigator turns raw button masks into these actions (with key repeat
for held directions) and knows nothing about Qt, so it's tested on its own;
ManagerGamepad is the Qt side, polling the pad (bridge/controller_bridge.py's
get_gamepad_state, XInput on Windows and joydev on Linux) and acting on the
focused widget.
"""

from __future__ import annotations

from controller_bridge import (
    STICK_DEADZONE,
    XINPUT_GAMEPAD_A,
    XINPUT_GAMEPAD_B,
    XINPUT_GAMEPAD_DPAD_DOWN,
    XINPUT_GAMEPAD_DPAD_LEFT,
    XINPUT_GAMEPAD_DPAD_RIGHT,
    XINPUT_GAMEPAD_DPAD_UP,
    XINPUT_GAMEPAD_LEFT_SHOULDER,
    XINPUT_GAMEPAD_RIGHT_SHOULDER,
)

UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"
ACTIVATE, BACK, PREV_PAGE, NEXT_PAGE = "activate", "back", "prev_page", "next_page"

BUTTON_ACTIONS = {
    XINPUT_GAMEPAD_DPAD_UP: UP,
    XINPUT_GAMEPAD_DPAD_DOWN: DOWN,
    XINPUT_GAMEPAD_DPAD_LEFT: LEFT,
    XINPUT_GAMEPAD_DPAD_RIGHT: RIGHT,
    XINPUT_GAMEPAD_A: ACTIVATE,
    XINPUT_GAMEPAD_B: BACK,
    XINPUT_GAMEPAD_LEFT_SHOULDER: PREV_PAGE,
    XINPUT_GAMEPAD_RIGHT_SHOULDER: NEXT_PAGE,
}
REPEATING = {UP, DOWN, LEFT, RIGHT}
REPEAT_DELAY = 0.45
REPEAT_INTERVAL = 0.12


def stick_directions(x: int, y: int, deadzone: int = STICK_DEADZONE) -> set[str]:
    """The left stick read as a d-pad (XInput's y grows upward)."""
    directions = set()
    if y > deadzone:
        directions.add(UP)
    elif y < -deadzone:
        directions.add(DOWN)
    if x > deadzone:
        directions.add(RIGHT)
    elif x < -deadzone:
        directions.add(LEFT)
    return directions


class GamepadNavigator:
    """Feed it what's held on each poll; it returns the actions to perform:
    each newly pressed one, and a held direction again after REPEAT_DELAY,
    every REPEAT_INTERVAL."""

    def __init__(self):
        self._held: dict[str, float] = {}  # action -> when it next repeats

    def update(self, buttons: int, stick_x: int, stick_y: int, now: float) -> list[str]:
        held = {action for bit, action in BUTTON_ACTIONS.items() if buttons & bit}
        held |= stick_directions(stick_x, stick_y)
        actions = []
        for action in sorted(held):
            if action not in self._held:
                actions.append(action)
                self._held[action] = now + REPEAT_DELAY
            elif action in REPEATING and now >= self._held[action]:
                actions.append(action)
                self._held[action] = now + REPEAT_INTERVAL
        for action in list(self._held):
            if action not in held:
                del self._held[action]
        return actions


class ManagerGamepad:
    """Polls the first controller and acts on the Manager's focused widget.
    Created once by the Manager window; costs one cheap poll per tick."""

    POLL_MS = 33

    def __init__(self, window):
        import time

        from PySide6.QtCore import QTimer

        self._time = time.monotonic
        self._window = window
        self._navigator = GamepadNavigator()
        self._timer = QTimer(window)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.POLL_MS)

    def _poll(self) -> None:
        from PySide6.QtWidgets import QApplication

        if QApplication.activeWindow() is None:
            # Not in front: forget held buttons so nothing fires on return.
            self._navigator = GamepadNavigator()
            return
        try:
            from controller_bridge import get_gamepad_state

            state = get_gamepad_state(0)
        except Exception:  # noqa: BLE001; no controller support here, nothing to do
            self._timer.stop()
            return
        if state is None:
            return
        for action in self._navigator.update(state.wButtons, state.sThumbLX, state.sThumbLY, self._time()):
            self._perform(action)

    def _perform(self, action: str) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QAbstractButton, QAbstractItemView, QApplication, QComboBox, QMenu

        if action in (PREV_PAGE, NEXT_PAGE):
            self._window.step_page(-1 if action == PREV_PAGE else 1)
            return
        focus = QApplication.focusWidget()
        target = QApplication.activePopupWidget() or focus or QApplication.activeWindow()
        if target is None:
            return

        def key(code, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
            for kind in (QKeyEvent.Type.KeyPress, QKeyEvent.Type.KeyRelease):
                QApplication.sendEvent(target, QKeyEvent(kind, code, modifiers))

        in_list = isinstance(target, (QAbstractItemView, QMenu, QComboBox)) or QApplication.activePopupWidget() is not None
        if action == BACK:
            key(Qt.Key.Key_Escape)
        elif action == ACTIVATE:
            if isinstance(target, QAbstractButton):
                target.click()
            else:
                key(Qt.Key.Key_Return)
        elif in_list and action in (UP, DOWN):
            key(Qt.Key.Key_Up if action == UP else Qt.Key.Key_Down)
        elif action in (UP, LEFT):
            key(Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
        else:
            key(Qt.Key.Key_Tab)

