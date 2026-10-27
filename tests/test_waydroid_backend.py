"""
Unit tests for the decision logic in bridge/waydroid_backend.py: the preflight
checks, reading `waydroid status`, and starting the container. The waydroid,
adb and pkexec calls are patched, so no Waydroid install is needed (the glue
that talks to a real one is only exercised on a real Linux machine).
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import waydroid_backend as wb  # noqa: E402; path set up above


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class _FakePath:
    """Stands in for pathlib.Path inside waydroid_backend so the /proc probes
    can be faked on any OS."""

    contents: dict[str, str] = {}
    existing: set[str] = set()

    def __init__(self, value):
        self.value = str(value).replace("\\", "/")

    def read_text(self, **_kwargs):
        if self.value in self.contents:
            return self.contents[self.value]
        raise OSError(self.value)

    def exists(self):
        return self.value in self.existing

    def is_file(self):
        return self.value in self.existing


class KernelHasBinderTests(unittest.TestCase):
    def _check(self, contents=None, existing=None) -> bool:
        fake = type("FakePath", (_FakePath,), {"contents": contents or {}, "existing": existing or set()})
        with patch.object(wb, "Path", fake):
            return wb.kernel_has_binder()

    def test_a_loaded_module_counts(self):
        self.assertTrue(self._check(contents={"/proc/modules": "binder_linux 262144 0 - Live 0x0\n"}))

    def test_binderfs_in_the_filesystems_list_counts(self):
        self.assertTrue(self._check(contents={"/proc/filesystems": "nodev\tbinder\n"}))

    def test_a_mounted_binder_device_counts(self):
        self.assertTrue(self._check(existing={"/dev/binderfs"}))
        self.assertTrue(self._check(existing={"/dev/binder"}))

    def test_nothing_means_no_binder(self):
        self.assertFalse(self._check(contents={"/proc/modules": "ext4 1 0\n", "/proc/filesystems": "nodev\ttmpfs\n"}))
        self.assertFalse(self._check())


class PreflightTests(unittest.TestCase):
    WAYLAND = {"WAYLAND_DISPLAY": "wayland-0"}

    def _preflight(self, which="/usr/bin/waydroid", binder=True, initialized=True, env=None):
        with patch.object(wb.shutil, "which", return_value=which), \
             patch.object(wb, "kernel_has_binder", return_value=binder), \
             patch.object(wb, "is_initialized", return_value=initialized), \
             patch.dict(os.environ, env if env is not None else self.WAYLAND, clear=True):
            return wb.preflight()

    def test_everything_in_place_has_no_problems(self):
        self.assertEqual(self._preflight(), [])

    def test_not_installed_stops_at_the_install_hint(self):
        self.assertEqual(self._preflight(which=None, binder=False, initialized=False), [wb.INSTALL_HINT])

    def test_a_kernel_without_binder_is_reported(self):
        self.assertEqual(self._preflight(binder=False), [wb.BINDER_HINT])

    def test_an_uninitialized_install_is_reported(self):
        problems = self._preflight(initialized=False)
        self.assertEqual(len(problems), 1)
        self.assertIn("waydroid init", problems[0])

    def test_a_non_wayland_session_is_reported(self):
        problems = self._preflight(env={"XDG_SESSION_TYPE": "x11"})
        self.assertEqual(len(problems), 1)
        self.assertIn("Wayland", problems[0])

    def test_a_wayland_session_type_alone_is_enough(self):
        self.assertEqual(self._preflight(env={"XDG_SESSION_TYPE": "Wayland"}), [])

    def test_several_problems_are_all_listed(self):
        self.assertEqual(len(self._preflight(binder=False, initialized=False, env={})), 3)


class StatusTests(unittest.TestCase):
    STATUS = "Session:\tRUNNING\nContainer:\tRUNNING\nVendor type:\tMAINLINE\n"

    def test_status_is_parsed(self):
        with patch.object(wb, "_waydroid", return_value=_done(self.STATUS)):
            self.assertEqual(wb.status()["session"], "RUNNING")

    def test_status_is_empty_when_waydroid_cannot_run(self):
        with patch.object(wb, "_waydroid", side_effect=OSError("not installed")):
            self.assertEqual(wb.status(), {})
        with patch.object(wb, "_waydroid", side_effect=subprocess.TimeoutExpired("waydroid", 15)):
            self.assertEqual(wb.status(), {})

    def test_container_running_includes_frozen(self):
        for container, expected in (("RUNNING", True), ("FROZEN", True), ("STOPPED", False), ("", False)):
            with self.subTest(container=container), patch.object(wb, "status", return_value={"container": container}):
                self.assertIs(wb.container_running(), expected)

    def test_session_running_needs_running(self):
        with patch.object(wb, "status", return_value={"session": "running"}):
            self.assertTrue(wb.session_running())
        with patch.object(wb, "status", return_value={"session": "STOPPED"}):
            self.assertFalse(wb.session_running())

    def test_is_running_needs_the_session_and_a_visible_vm(self):
        devices = "List of devices attached\n192.168.240.112:5555\tdevice\n"
        with patch.object(wb, "session_running", return_value=True), patch.object(wb, "_run", return_value=_done(devices)):
            self.assertTrue(wb.is_running())
        with patch.object(wb, "session_running", return_value=True), \
             patch.object(wb, "_run", return_value=_done("List of devices attached\n\n")):
            self.assertFalse(wb.is_running())
        with patch.object(wb, "session_running", return_value=False), patch.object(wb, "_run") as run:
            self.assertFalse(wb.is_running())
        run.assert_not_called()

    def test_is_running_is_false_when_adb_is_missing(self):
        with patch.object(wb, "session_running", return_value=True), patch.object(wb, "_run", side_effect=OSError("no adb")):
            self.assertFalse(wb.is_running())


class EnsureContainerRunningTests(unittest.TestCase):
    def test_an_already_running_container_is_left_alone(self):
        with patch.object(wb, "container_running", return_value=True), patch.object(wb, "_run") as run:
            wb.ensure_container_running()
        run.assert_not_called()

    def test_without_pkexec_it_raises_with_the_command_to_run(self):
        with patch.object(wb, "container_running", return_value=False), patch.object(wb.shutil, "which", return_value=None):
            with self.assertRaises(wb.WaydroidError) as caught:
                wb.ensure_container_running()
        self.assertIn(wb.CONTAINER_UNIT, str(caught.exception))

    def test_it_asks_polkit_to_start_the_unit_and_waits_for_it(self):
        states = iter([False, False, True])
        with patch.object(wb, "container_running", side_effect=lambda: next(states)), \
             patch.object(wb.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(wb, "_run", return_value=_done()) as run, \
             patch.object(wb.time, "sleep"):
            wb.ensure_container_running()
        self.assertEqual(run.call_args.args[0], ["pkexec", "systemctl", "start", wb.CONTAINER_UNIT])

    def test_a_refused_prompt_raises(self):
        with patch.object(wb, "container_running", return_value=False), \
             patch.object(wb.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(wb, "_run", return_value=_done(returncode=126)):
            with self.assertRaises(wb.WaydroidError):
                wb.ensure_container_running()


if __name__ == "__main__":
    unittest.main()
