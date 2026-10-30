"""
Unit tests for bridge/launch_bridge.py's adb use: launching iiSU at bridge
start (one on-device wait for the boot animation, then `am start` retried until
Android accepts it, with each stage timed as its marker arrives) and the media
volume helpers used at every game launch. The adb client is patched and time is
a fake clock, so this needs no VM. Imports launch_bridge, which pulls in the
Windows window helpers.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bridge"))

import launch_bridge  # noqa: E402; path set up above


class FakeTime:
    def __init__(self):
        self.now = 50.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class LaunchIisuTests(unittest.TestCase):
    def _launch(self, events, waydroid=False):
        """events: (seconds that pass on the device, line it prints) pairs."""
        clock = FakeTime()
        scripts = []

        def shell_lines(script, timeout):
            scripts.append((script, timeout))
            for seconds, line in events:
                clock.now += seconds
                yield line

        with patch.object(launch_bridge, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)), \
             patch.object(launch_bridge.adb_client, "shell_lines", side_effect=shell_lines), \
             patch.object(launch_bridge, "_is_waydroid", return_value=waydroid), \
             patch.object(launch_bridge, "set_volume_max") as volume, \
             patch("builtins.print") as printed:
            launch_bridge.launch_iisu({"iisu_component": "com.iisulauncher/.launcher.X"})
        return [call.args[0] for call in printed.call_args_list], volume, scripts

    def test_times_each_stage_as_its_marker_arrives(self):
        lines, volume, _ = self._launch([
            (2.0, "bootanim_done"), (1.0, "started_attempt=2"), (0.0, "__iisupc_exit=0"),
        ])
        self.assertIn("iiSU started 3.0s after asking", lines[0])
        self.assertIn("boot animation wait 2.0s", lines[0])
        self.assertIn("am start attempt 2", lines[0])
        volume.assert_called_once()

    def test_a_failed_start_reports_what_android_said(self):
        lines, volume, _ = self._launch([
            (0.5, "bootanim_done"), (60.0, "Error: Activity class {com.iisulauncher/.launcher.X} does not exist."),
            (0.0, "__iisupc_exit=1"),
        ])
        self.assertTrue(lines[0].startswith("[bridge] could not launch iiSU"))
        self.assertIn("does not exist", lines[1])
        volume.assert_not_called()

    def test_no_answer_from_adb_is_reported(self):
        lines, volume, _ = self._launch([])
        self.assertIn("no answer from adb", lines[1])
        volume.assert_not_called()

    def test_the_device_script_carries_the_component_and_the_waits(self):
        _, _, scripts = self._launch([(0, "__iisupc_exit=1")])
        script, timeout = scripts[0]
        self.assertIn("am start -n com.iisulauncher/.launcher.X", script)
        self.assertIn(f"+ {launch_bridge.BOOTANIM_WAIT_SECONDS} ))", script)
        self.assertIn(f"+ {launch_bridge.IISU_START_WAIT_SECONDS} ))", script)
        self.assertGreater(timeout, launch_bridge.BOOTANIM_WAIT_SECONDS + launch_bridge.IISU_START_WAIT_SECONDS)

    def test_waydroid_does_not_wait_for_a_boot_animation(self):
        _, _, scripts = self._launch([(0, "__iisupc_exit=1")], waydroid=True)
        self.assertIn("+ 0 ))", scripts[0][0])


CHROME_INTENT = (
    "Intent { act=android.intent.action.MAIN cat=[android.intent.category.LAUNCHER] flg=0x10000000 "
    "cmp=com.android.chrome/com.google.android.apps.chrome.Main }"
)
GAME_INTENT = (
    "Intent { act=android.intent.action.VIEW dat=content://x/y.chd flg=0x10000001 "
    "cmp=com.github.stenzek.duckstation/.EmulationActivity }\nCLIPURI:content://x/y.chd"
)


class AndroidHandBackTests(unittest.TestCase):
    CONFIG = {"emulators": {"com.github.stenzek.duckstation": {}, "com.retroarch": {}}}

    def test_an_android_app_goes_back_to_android(self):
        for intent in (CHROME_INTENT, CHROME_INTENT.replace("com.android.chrome/com.google.android.apps.chrome.Main",
                                                            "com.android.vending/.AssetBrowserActivity")):
            with self.subTest(intent=intent[-40:]):
                self.assertTrue(launch_bridge.belongs_on_android(intent, self.CONFIG))

    def test_what_the_pc_handles_stays_on_the_pc(self):
        self.assertFalse(launch_bridge.belongs_on_android(GAME_INTENT, self.CONFIG))
        retroarch = "Intent { act=android.intent.action.MAIN cmp=com.retroarch.aarch64/.Main }\nEXTRA:ROM=/x.n64"
        self.assertFalse(launch_bridge.belongs_on_android(retroarch, self.CONFIG), "keys match as prefixes")
        for package in ("com.winlator.cmod", "app.gamenative"):
            with self.subTest(package=package):
                self.assertFalse(launch_bridge.belongs_on_android(f"Intent {{ cmp={package}/.Main }}", self.CONFIG))

    def test_an_intent_naming_no_app_is_for_android_to_resolve(self):
        self.assertTrue(launch_bridge.belongs_on_android("Intent { act=android.intent.action.VIEW dat=https://x }", self.CONFIG))

    CAPABLE = "\nBRIDGE_REPLY:1"

    def _answer(self, intent, config=CONFIG, send_error=None, adb=(0, "Starting: Intent { ... }")):
        conn = SimpleNamespace(sent=[])
        conn.sendall = (lambda data: (_ for _ in ()).throw(send_error)) if send_error else conn.sent.append
        loader = (lambda: (_ for _ in ()).throw(launch_bridge.ConfigMissingError())) if config is None else (lambda: config)
        with patch.object(launch_bridge, "load_config", side_effect=loader), \
             patch.object(launch_bridge.adb_client, "shell", return_value=adb) as shell, \
             patch.object(launch_bridge, "log_launch") as log:
            handed_back = launch_bridge.answer_launch(conn, intent)
        self.shell = shell
        return handed_back, conn.sent, log

    def test_the_reply_comes_before_any_launch_work(self):
        handed_back, sent, log = self._answer(CHROME_INTENT + self.CAPABLE)
        self.assertTrue(handed_back)
        self.assertEqual(sent, [b"A"])
        log.assert_called_once_with("ANDROID: handed com.android.chrome back to Android")
        self.shell.assert_not_called()
        handed_back, sent, log = self._answer(GAME_INTENT + self.CAPABLE)
        self.assertFalse(handed_back)
        self.assertEqual(sent, [b"P"])
        log.assert_not_called()

    def test_an_unreadable_config_keeps_the_launch_here(self):
        handed_back, sent, _ = self._answer(CHROME_INTENT + self.CAPABLE, config=None)
        self.assertFalse(handed_back)
        self.assertEqual(sent, [b"P"])

    def test_iisu_giving_up_on_the_answer_is_fine(self):
        handed_back, _, _ = self._answer(GAME_INTENT + self.CAPABLE, send_error=ConnectionResetError())
        self.assertFalse(handed_back)

    def test_for_an_older_patch_the_bridge_starts_the_app_itself(self):
        # Patched before the answer existed: it never reads one, so without
        # this its Android apps could only be fixed by re-patching iiSU.
        handed_back, sent, log = self._answer(CHROME_INTENT)
        self.assertTrue(handed_back)
        self.assertEqual(sent, [], "nothing is sent to an iiSU that won't read it")
        self.assertEqual(
            self.shell.call_args.args[0],
            "am start -n com.android.chrome/com.google.android.apps.chrome.Main "
            "-a android.intent.action.MAIN -c android.intent.category.LAUNCHER -f 0x10000000",
        )
        log.assert_called_once_with("ANDROID: started com.android.chrome on Android for an older iiSU patch")

    def test_an_older_patch_game_is_untouched_here(self):
        handed_back, sent, _ = self._answer(GAME_INTENT)
        self.assertFalse(handed_back)
        self.assertEqual(sent, [])
        self.shell.assert_not_called()

    def test_a_failed_android_start_is_reported(self):
        handed_back, _, log = self._answer(CHROME_INTENT, adb=(0, "Error: Activity class does not exist."))
        self.assertTrue(handed_back)
        self.assertIn("FAILED: couldn't start com.android.chrome on Android", log.call_args.args[0])

    def test_the_start_command_is_quoted_for_the_device_shell(self):
        command = launch_bridge.android_start_command("Intent { cmp=com.x/.Main;reboot }")
        self.assertEqual(command, "am start -n 'com.x/.Main;reboot'")


class StandInTests(unittest.TestCase):
    """iiSU never leaves the screen for a PC game, so it never gets the return
    that restores its music and interface. The emulator's stand-in covers it
    during the game and closes when the game ends."""

    def setUp(self):
        launch_bridge.held_stub_package = None
        self.addCleanup(setattr, launch_bridge, "held_stub_package", None)

    def test_the_stand_in_covers_iisu_then_closes_on_return(self):
        with patch.object(launch_bridge.adb_client, "shell", return_value=(0, "Starting: Intent { ... }")) as shell:
            launch_bridge.hold_iisu_behind_stub("com.github.stenzek.duckstation")
            launch_bridge.release_iisu_from_stub()
            launch_bridge.release_iisu_from_stub()
        commands = [call.args[0] for call in shell.call_args_list]
        self.assertEqual(commands, [
            "am start -n com.github.stenzek.duckstation/com.iisupc.stub.RedirectorActivity --ez com.iisupc.stub.HOLD true",
            "am start -n com.github.stenzek.duckstation/com.iisupc.stub.RedirectorActivity",
        ], "closed once, by a start without HOLD")

    def test_a_package_with_no_stand_in_is_skipped(self):
        answer = (0, "Error: Activity class {com.android.chrome/com.iisupc.stub.RedirectorActivity} does not exist.")
        with patch.object(launch_bridge.adb_client, "shell", return_value=answer) as shell, \
             patch.object(launch_bridge, "debug_log"):
            launch_bridge.hold_iisu_behind_stub("com.android.chrome")
            launch_bridge.release_iisu_from_stub()
            launch_bridge.hold_iisu_behind_stub(None)
        self.assertEqual(shell.call_count, 1, "nothing to close, and no package means no start")

    def test_returning_to_iisu_closes_it(self):
        launch_bridge.held_stub_package = "org.ppsspp.ppsspp"
        with patch.object(launch_bridge.adb_client, "shell", return_value=(0, "")) as shell, \
             patch.object(launch_bridge, "_adb_media_volume_set"), \
             patch("builtins.print"):
            launch_bridge.end_game_handoff()
        self.assertEqual(shell.call_args.args[0], "am start -n org.ppsspp.ppsspp/com.iisupc.stub.RedirectorActivity")


class QuitTapTests(unittest.TestCase):
    def test_quitting_a_subprocess_emulator_is_logged_and_terminates_it(self):
        proc = SimpleNamespace(pid=4321, poll=lambda: None, terminated=False)
        proc.terminate = lambda: setattr(proc, "terminated", True)
        with patch.object(launch_bridge, "current_process", proc), \
             patch.object(launch_bridge, "current_native_window", None), \
             patch.object(launch_bridge, "current_native_pid", None), \
             patch.object(launch_bridge, "game_handoff_active", False), \
             patch.object(launch_bridge, "log_launch") as log, \
             patch.object(launch_bridge, "shutdown_everything") as shutdown, \
             patch("builtins.print"):
            launch_bridge.quit_tap_action("escape")
        self.assertTrue(proc.terminated)
        shutdown.assert_not_called()
        log.assert_called_once_with("QUIT: escape, terminating emulator PID 4321")


class MediaVolumeTests(unittest.TestCase):
    def test_reads_the_volume_from_androids_answer(self):
        with patch.object(launch_bridge.adb_client, "shell", return_value=(0, "volume is 7 in range [0..15]")):
            self.assertEqual(launch_bridge._adb_media_volume_get(), 7)

    def test_an_unreadable_answer_is_none(self):
        with patch.object(launch_bridge.adb_client, "shell", return_value=(None, "")):
            self.assertIsNone(launch_bridge._adb_media_volume_get())

    def test_setting_reports_success_and_never_goes_negative(self):
        with patch.object(launch_bridge.adb_client, "shell", return_value=(0, "")) as shell:
            self.assertTrue(launch_bridge._adb_media_volume_set(-3))
        self.assertIn("--set 0 ", shell.call_args.args[0])
        with patch.object(launch_bridge.adb_client, "shell", return_value=(1, "")):
            self.assertFalse(launch_bridge._adb_media_volume_set(5))


if __name__ == "__main__":
    unittest.main()
