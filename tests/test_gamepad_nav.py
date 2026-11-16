"""bridge/ui/gamepad_nav.py's GamepadNavigator: controller buttons and the
left stick turned into Manager actions, with key repeat for held directions."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from bridge.ui import gamepad_nav as nav  # noqa: E402
from controller_bridge import XINPUT_GAMEPAD_A, XINPUT_GAMEPAD_DPAD_DOWN, XINPUT_GAMEPAD_RIGHT_SHOULDER  # noqa: E402


class GamepadNavigatorTests(unittest.TestCase):
    def test_a_press_activates_once(self):
        navigator = nav.GamepadNavigator()
        self.assertEqual(navigator.update(XINPUT_GAMEPAD_A, 0, 0, 0.0), [nav.ACTIVATE])
        self.assertEqual(navigator.update(XINPUT_GAMEPAD_A, 0, 0, 5.0), [], "A doesn't repeat")
        navigator.update(0, 0, 0, 5.1)
        self.assertEqual(navigator.update(XINPUT_GAMEPAD_A, 0, 0, 5.2), [nav.ACTIVATE])

    def test_held_direction_repeats_after_a_delay(self):
        navigator = nav.GamepadNavigator()
        self.assertEqual(navigator.update(XINPUT_GAMEPAD_DPAD_DOWN, 0, 0, 0.0), [nav.DOWN])
        self.assertEqual(navigator.update(XINPUT_GAMEPAD_DPAD_DOWN, 0, 0, 0.2), [])
        self.assertEqual(navigator.update(XINPUT_GAMEPAD_DPAD_DOWN, 0, 0, nav.REPEAT_DELAY + 0.01), [nav.DOWN])
        self.assertEqual(navigator.update(XINPUT_GAMEPAD_DPAD_DOWN, 0, 0, nav.REPEAT_DELAY + 0.05), [])
        self.assertEqual(
            navigator.update(XINPUT_GAMEPAD_DPAD_DOWN, 0, 0, nav.REPEAT_DELAY + nav.REPEAT_INTERVAL + 0.02), [nav.DOWN]
        )

    def test_stick_acts_as_dpad(self):
        self.assertEqual(nav.stick_directions(0, 30000), {nav.UP})
        self.assertEqual(nav.stick_directions(-30000, 0), {nav.LEFT})
        self.assertEqual(nav.stick_directions(1000, -1000), set(), "inside the deadzone")
        navigator = nav.GamepadNavigator()
        self.assertEqual(navigator.update(0, 0, -30000, 0.0), [nav.DOWN])

    def test_shoulder_changes_page(self):
        self.assertEqual(nav.GamepadNavigator().update(XINPUT_GAMEPAD_RIGHT_SHOULDER, 0, 0, 0.0), [nav.NEXT_PAGE])
