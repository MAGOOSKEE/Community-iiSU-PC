"""
Unit tests for bridge/controller_bridge.py's platform-agnostic pieces
(the quit-chord bitmask and the Linux gamepad state shape), no real
controller hardware needed, same as this project's other tests.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import controller_bridge


class ControllerBridgeTests(unittest.TestCase):
    def test_linux_gamepad_state_initializes_all_fields_to_zero(self):
        state = controller_bridge.LinuxGamepadState()
        self.assertEqual(state.wButtons, 0)
        self.assertEqual(state.sThumbLX, 0)
        self.assertEqual(state.sThumbLY, 0)
        self.assertEqual(state.sThumbRX, 0)
        self.assertEqual(state.sThumbRY, 0)
        self.assertEqual(state.bLeftTrigger, 0)
        self.assertEqual(state.bRightTrigger, 0)

    def test_quit_chord_mask_matches_select_start(self):
        mask = controller_bridge.quit_chord_mask(["select", "start"])
        expected = controller_bridge.XINPUT_GAMEPAD_BACK | controller_bridge.XINPUT_GAMEPAD_START
        self.assertEqual(mask, expected)

    def test_get_gamepad_state_returns_none_for_an_unused_slot(self):
        self.assertIsNone(controller_bridge.get_gamepad_state(99))


if __name__ == "__main__":
    unittest.main()
