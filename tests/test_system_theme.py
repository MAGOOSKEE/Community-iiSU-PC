import unittest

import bridge.ui  # noqa: F401
from bridge import system_theme


class AndroidThemeSyncTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(system_theme.android_night_command("light"), "cmd uimode night no")
        self.assertEqual(system_theme.android_night_command("dark"), "cmd uimode night yes")

    def test_sync_reports_adb_result(self):
        sent = []

        def shell(command, timeout=None):
            sent.append(command)
            return 0, "Night mode: yes"

        self.assertEqual(system_theme.sync_android_theme(shell, "dark"), ("dark", True))
        self.assertEqual(sent, ["cmd uimode night yes"])
        self.assertEqual(system_theme.sync_android_theme(lambda c, timeout=None: (1, "err"), "light"), ("light", False))

    def test_detect_returns_light_or_dark(self):
        self.assertIn(system_theme.detect_theme(), ("light", "dark"))


if __name__ == "__main__":
    unittest.main()
