"""
Unit tests for bridge/controller_bridge.py's platform-agnostic pieces
(the quit-chord bitmask and the Linux gamepad state shape), no real
controller hardware needed, same as this project's other tests.
"""

import sys
import tempfile
import unittest
import unittest.mock
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


class ControllerNoticeTests(unittest.TestCase):
    """A PlayStation controller that needs Steam Input gets a notice, never
    Steam opened for it, and the notice can be switched off for good."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        state_path = Path(self._tmp.name) / ".controller_notice.json"
        patcher = unittest.mock.patch.object(controller_bridge, "CONTROLLER_NOTICE_STATE_PATH", state_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state_path = state_path

    def test_the_notice_is_a_popup_and_never_opens_steam(self):
        self.assertFalse(hasattr(controller_bridge, "open_steam_for_controller_setup"))
        with unittest.mock.patch.object(controller_bridge.subprocess, "Popen") as popen:
            self.assertTrue(controller_bridge.notify_unmapped_sony_controller("DualSense Wireless Controller"))
        command = popen.call_args.args[0]
        self.assertEqual(command[1:], ["-m", "bridge.ui.controller_notice_app", "DualSense Wireless Controller"])
        self.assertIn("DualSense Wireless Controller", controller_bridge.controller_notice_message("DualSense Wireless Controller"))

    def test_dont_show_again_is_remembered(self):
        self.assertFalse(controller_bridge.controller_notice_dismissed())
        controller_bridge.dismiss_controller_notice()
        self.assertTrue(controller_bridge.controller_notice_dismissed())
        with unittest.mock.patch.object(controller_bridge.subprocess, "Popen") as popen:
            self.assertFalse(controller_bridge.notify_unmapped_sony_controller("DualSense Wireless Controller"))
        popen.assert_not_called()

    def test_a_damaged_state_file_shows_the_notice_again(self):
        self.state_path.write_text("{not json", encoding="utf-8")
        self.assertFalse(controller_bridge.controller_notice_dismissed())

    def test_each_controller_is_notified_once_while_it_stays_plugged_in(self):
        bridge = controller_bridge.ControllerBridge(lambda: False, lambda label: None)
        with unittest.mock.patch.object(controller_bridge, "find_unmapped_sony_controllers", return_value=["DualSense"]), \
             unittest.mock.patch.object(controller_bridge, "notify_unmapped_sony_controller") as notify:
            bridge._check_sony_controllers(100.0)
            bridge._check_sony_controllers(200.0)
        notify.assert_called_once_with("DualSense")


if __name__ == "__main__":
    unittest.main()
