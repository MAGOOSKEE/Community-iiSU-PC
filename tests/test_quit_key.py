"""The quit key: left to a Windows or Steam game on a tap and acted on when
held, a polite close before a forced one, and ignored while another program
is in front. Also the launch bridge's "is the VM gone" check."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import launch_bridge as lb  # noqa: E402
from shared import vm_backend as vb  # noqa: E402

CONFIG = {"iisu_window_title": "iisuwin", "quit_hotkey": {"modifiers": [], "key": "escape"}}


class StateTestCase(unittest.TestCase):
    """Puts the bridge's tracking globals back after each test."""

    NAMES = ("current_process", "current_native_window", "current_native_pid", "current_native_is_app", "game_handoff_active")

    def setUp(self):
        saved = {name: getattr(lb, name) for name in self.NAMES}
        self.addCleanup(lambda: [setattr(lb, name, value) for name, value in saved.items()])
        for name in self.NAMES:
            setattr(lb, name, False if name in ("current_native_is_app", "game_handoff_active") else None)


class QuitKeyAppliesTests(StateTestCase):
    def _applies(self, foreground_pid, foreground_name="other.exe", iisu_pid=500):
        windows = {10: foreground_pid, 20: iisu_pid}
        with patch.object(lb.user32, "GetForegroundWindow", return_value=10 if foreground_pid else 0), \
             patch.object(lb, "_window_pid", side_effect=lambda hwnd: windows.get(hwnd)), \
             patch.object(lb, "find_window_by_title", return_value=20), \
             patch.object(lb, "window_process_name", return_value=foreground_name), \
             patch.object(lb, "debug_log"), patch("builtins.print"):
            return lb.quit_key_applies(CONFIG, "ESCAPE")

    def test_iisu_in_front_counts(self):
        self.assertTrue(self._applies(500))

    def test_another_program_in_front_does_not(self):
        self.assertFalse(self._applies(999, "chrome.exe"))

    def test_the_running_emulator_in_front_counts(self):
        lb.current_process = SimpleNamespace(pid=700, args=["C:/emu/duckstation-qt.exe"], poll=lambda: None)
        self.assertTrue(self._applies(700, "duckstation-qt.exe"))

    def test_an_emulator_window_owned_by_a_process_it_started_counts(self):
        lb.current_process = SimpleNamespace(pid=700, args=["C:/emu/Ryujinx.exe"], poll=lambda: None)
        self.assertTrue(self._applies(701, "ryujinx.exe"))

    def test_the_tracked_windows_game_in_front_counts(self):
        lb.current_native_pid = 800
        self.assertTrue(self._applies(800, "game.exe"))

    def test_whatever_is_in_front_counts_while_a_game_is_still_opening(self):
        lb.game_handoff_active = True
        self.assertTrue(self._applies(999, "steamwebhelper.exe"))

    def test_nothing_in_front_never_blocks_the_key(self):
        self.assertTrue(self._applies(None))

    def test_the_manager_does_not_count(self):
        # Its own Python process, but Escape there closes a dialog.
        self.assertFalse(self._applies(321, "pythonw.exe"))


class NativeAppRunningTests(StateTestCase):
    def test_an_emulator_is_not_a_native_app(self):
        lb.current_native_window, lb.current_native_pid = 5, 800  # a relaunched emulator
        self.assertFalse(lb.native_app_running())

    def test_a_tracked_windows_game_is(self):
        lb.current_native_is_app, lb.current_native_pid = True, 800
        self.assertTrue(lb.native_app_running())

    def test_so_is_one_still_opening(self):
        lb.current_native_is_app, lb.game_handoff_active = True, True
        self.assertTrue(lb.native_app_running())

    def test_ending_the_handoff_clears_it(self):
        lb.current_native_is_app = True
        with patch.object(lb, "release_iisu_from_stub"), patch.object(lb, "debug_log"), patch("builtins.print"):
            lb.end_game_handoff()
        self.assertFalse(lb.current_native_is_app)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class QuitKeyWatcherTests(StateTestCase):
    def _watch(self, presses, native_app=False, applies=True):
        """Runs the watcher over a list of key states, one per poll, and
        returns the labels quit_tap_action was called with."""
        clock = Clock()
        states = iter(presses)

        class Done(Exception):
            pass

        def key_down(_vk):
            try:
                return next(states)
            except StopIteration:
                raise Done from None

        with patch.object(lb, "_is_key_down", side_effect=key_down), \
             patch.object(lb, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)), \
             patch.object(lb, "native_app_running", return_value=native_app), \
             patch.object(lb, "quit_key_applies", return_value=applies), \
             patch.object(lb, "quit_tap_action") as action, \
             patch.object(lb, "debug_log"), patch("builtins.print"):
            with self.assertRaises(Done):
                lb.quit_key_watcher(CONFIG)
        return [call.args[0] for call in action.call_args_list]

    @staticmethod
    def _held(seconds):
        return [True] * int(seconds / lb.KEY_POLL_INTERVAL)

    def test_a_tap_quits_an_emulator(self):
        self.assertEqual(self._watch([False, True, True, False, False]), ["ESCAPE tapped"])

    def test_a_tap_in_a_windows_game_is_left_to_the_game(self):
        self.assertEqual(self._watch([False, True, True, False, False], native_app=True), [])

    def test_holding_in_a_windows_game_quits_it_once(self):
        presses = [False] + self._held(lb.NATIVE_QUIT_HOLD_SECONDS + 1.0) + [False, False]
        self.assertEqual(self._watch(presses, native_app=True), ["ESCAPE held"])

    def test_a_hold_just_short_of_the_time_does_nothing(self):
        presses = [False] + self._held(lb.NATIVE_QUIT_HOLD_SECONDS - 0.3) + [False]
        self.assertEqual(self._watch(presses, native_app=True), [])

    def test_a_press_with_another_program_in_front_does_nothing(self):
        self.assertEqual(self._watch([False, True, False, False], applies=False), [])
        presses = [False] + self._held(lb.NATIVE_QUIT_HOLD_SECONDS + 1.0) + [False]
        self.assertEqual(self._watch(presses, native_app=True, applies=False), [])


class CloseNativeAppTests(unittest.TestCase):
    def _close(self, window_ok, running):
        """running: what _pid_is_running answers, in order."""
        answers = iter(running)
        clock = Clock()

        class InlineThread:
            def __init__(self, target, daemon=None):
                self._target = target

            def start(self):
                self._target()

        with patch.object(lb.user32, "IsWindow", return_value=window_ok), \
             patch.object(lb, "close_window", return_value=window_ok) as close, \
             patch.object(lb, "_pid_is_running", side_effect=lambda pid: next(answers, True)), \
             patch.object(lb, "_terminate_native_pid") as kill, \
             patch.object(lb, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)), \
             patch.object(lb.threading, "Thread", InlineThread), \
             patch.object(lb, "log_launch"):
            lb._close_native_app(77, 800)
        return close, kill

    def test_a_game_that_closes_when_asked_is_never_forced(self):
        close, kill = self._close(True, [True, True, False])
        close.assert_called_once_with(77)
        kill.assert_not_called()

    def test_a_game_that_ignores_the_request_is_ended_after_the_grace_time(self):
        _close, kill = self._close(True, [])
        kill.assert_called_once_with(800)

    def test_with_no_window_to_ask_it_is_ended_straight_away(self):
        _close, kill = self._close(False, [])
        kill.assert_called_once_with(800)


class VmDeviceListedTests(unittest.TestCase):
    def test_offline_emulator_still_counts_as_listed(self):
        self.assertTrue(vb.vm_device_listed("List of devices attached\nemulator-5554\toffline\n"))

    def test_ready_emulator_is_listed(self):
        self.assertTrue(vb.vm_device_listed("List of devices attached\nemulator-5554\tdevice\n"))

    def test_empty_list_and_usb_phone_are_not(self):
        self.assertFalse(vb.vm_device_listed("List of devices attached\n\n"))
        self.assertFalse(vb.vm_device_listed("List of devices attached\nR5CT1234ABC\tdevice\n"))


if __name__ == "__main__":
    unittest.main()
