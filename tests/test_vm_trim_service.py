"""Unit tests for bridge/services/vm_trim_service.py, against a scripted fake adb."""

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))
sys.path.insert(0, str(PROJECT_ROOT))

from services import vm_trim_service as svc  # noqa: E402; path set up above
from shared import vm_trim as vt  # noqa: E402


def done(out="", rc=0, err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class FakeVm:
    """A tiny Android: installed packages, a default keyboard, and switches for
    the failure modes that matter (sick after a removal, boot loop on reboot)."""

    FINGERPRINT = next(iter(vt.VERIFIED_FINGERPRINTS))

    def __init__(self, installed, healthy=True, keyboard_install_ok=True, refuse=(), bootloop_on_reboot=False,
                 sick_after_uninstall=False, fingerprint=None):
        self.installed = set(installed)
        self.all_known = set(installed)
        self.default_ime = "com.google.android.inputmethod.latin/x"
        self.healthy = healthy
        self.keyboard_install_ok = keyboard_install_ok
        self.refuse = set(refuse)
        self.bootloop_on_reboot = bootloop_on_reboot
        self.sick_after_uninstall = sick_after_uninstall
        self.fingerprint = fingerprint or self.FINGERPRINT
        self.booted = True
        self.log: list[str] = []

    def __call__(self, *args, timeout=30):
        command = " ".join(args)
        self.log.append(command)
        if command == "shell pm list packages":
            return done("".join(f"package:{p}\n" for p in sorted(self.installed)))
        if command == "shell pm list packages -u":
            return done("".join(f"package:{p}\n" for p in sorted(self.all_known)))
        if command.startswith("shell pm uninstall --user 0 "):
            package = command.rsplit(" ", 1)[1]
            if package in self.refuse or package not in self.installed:
                return done("Failure [DELETE_FAILED_INTERNAL_ERROR]")
            self.installed.discard(package)
            if self.sick_after_uninstall:
                self.healthy = False
            return done("Success")
        if command.startswith("shell cmd package install-existing --user 0 "):
            package = command.rsplit(" ", 1)[1]
            if package in self.all_known:
                self.installed.add(package)
                self.healthy = True
                return done(f"Package {package} installed for user: 0")
            return done("Failure")
        if args[0] == "install":
            if not self.keyboard_install_ok:
                return done("Failure [INSTALL_FAILED]")
            self.installed.add(vt.LEAN_KEYBOARD_PACKAGE)
            self.all_known.add(vt.LEAN_KEYBOARD_PACKAGE)
            return done("Performing Streamed Install\nSuccess")
        if command.startswith("shell ime set "):
            self.default_ime = args[3]
            return done()
        if command == "shell settings get secure default_input_method":
            return done(self.default_ime + "\n")
        if command == "reboot":
            self.booted = not self.bootloop_on_reboot
            return done()
        if command == "shell getprop ro.build.fingerprint":
            return done(self.fingerprint + "\n")
        if command == "shell getprop sys.boot_completed":
            return done("1\n" if self.booted else "0\n")
        if command == "shell pidof com.android.systemui":
            return done("123\n" if (self.booted and self.healthy) else "")
        if command.startswith("shell logcat -b crash"):
            return done("")
        return done()


class ReadingTests(unittest.TestCase):
    def test_removed_for_user0_is_the_difference(self):
        vm = FakeVm({"a", "b"})
        vm.installed.discard("b")
        self.assertEqual(svc.removed_for_user0(vm), {"b"})


class ApplyTests(unittest.TestCase):
    def test_removes_only_installed_tier_members_and_reports_failures(self):
        vm = FakeVm({"com.google.android.apps.maps", "com.google.android.youtube", "com.android.chrome"},
                    refuse={"com.google.android.youtube"})
        result = svc.apply_tier(vm, "apps", keyboard_ready=False)
        self.assertEqual(result.removed, ["com.google.android.apps.maps"])
        self.assertIn("com.google.android.youtube", result.failed)
        self.assertIn("com.android.chrome", vm.installed)

    def test_restore_brings_packages_back(self):
        vm = FakeVm({"com.google.android.apps.maps"})
        svc.apply_tier(vm, "apps", False)
        self.assertNotIn("com.google.android.apps.maps", vm.installed)
        self.assertEqual(svc.restore_packages(vm, ["com.google.android.apps.maps"]), [])
        self.assertIn("com.google.android.apps.maps", vm.installed)

    def test_restoring_something_unknown_is_reported(self):
        self.assertEqual(svc.restore_packages(FakeVm(set()), ["com.nope"]), ["com.nope"])


class MakeLeanTests(unittest.TestCase):
    INSTALLED = {"com.google.android.apps.maps", "com.google.android.googlequicksearchbox", vt.GBOARD,
                 "com.android.chrome", "com.android.vending", "com.android.settings"}
    NO_WAIT = staticmethod(lambda _s: None)

    def _apk(self, tmp):
        apk = Path(tmp) / "k.apk"
        apk.write_bytes(b"x")
        return apk

    def _run(self, vm, apk=None, **kw):
        return svc.make_lean(vm, apk, sleep=self.NO_WAIT, **kw)

    def test_full_profile_installs_the_keyboard_then_removes_gboard_and_the_tiers(self):
        vm = FakeVm(self.INSTALLED)
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(vm, self._apk(tmp))
        self.assertEqual(report.keyboard, "installed and set as default")
        self.assertEqual(vm.default_ime, vt.LEAN_KEYBOARD_IME)
        self.assertNotIn(vt.GBOARD, vm.installed)
        self.assertNotIn("com.google.android.apps.maps", vm.installed)
        self.assertNotIn("com.google.android.googlequicksearchbox", vm.installed)
        for kept in ("com.android.chrome", "com.android.vending", "com.android.settings", vt.LEAN_KEYBOARD_PACKAGE):
            self.assertIn(kept, vm.installed)
        self.assertEqual(report.removed_count, 3)
        self.assertFalse(report.needs_rebuild)

    def test_gboard_is_removed_only_after_the_keyboard_is_in_place(self):
        vm = FakeVm(self.INSTALLED)
        with tempfile.TemporaryDirectory() as tmp:
            self._run(vm, self._apk(tmp))
        install_at = next(i for i, c in enumerate(vm.log) if c.startswith("install"))
        gboard_at = next(i for i, c in enumerate(vm.log) if c.endswith(vt.GBOARD) and "uninstall" in c)
        self.assertLess(install_at, gboard_at)

    def test_a_failed_keyboard_install_keeps_gboard(self):
        vm = FakeVm(self.INSTALLED, keyboard_install_ok=False)
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(vm, self._apk(tmp))
        self.assertIn("Gboard kept", report.keyboard)
        self.assertIn(vt.GBOARD, vm.installed)

    def test_without_a_keyboard_apk_gboard_is_never_touched(self):
        vm = FakeVm(self.INSTALLED)
        self._run(vm, None)
        self.assertIn(vt.GBOARD, vm.installed)

    def test_every_tier_is_verified_across_a_reboot_after_waiting_for_android_to_save(self):
        waits = []
        vm = FakeVm(self.INSTALLED)
        svc.make_lean(vm, None, sleep=waits.append)
        reboots = [i for i, c in enumerate(vm.log) if c == "reboot"]
        self.assertEqual(len(reboots), 2)  # one per tier
        self.assertIn(svc.PERSIST_SECONDS, waits)
        first_uninstall = next(i for i, c in enumerate(vm.log) if "uninstall" in c)
        self.assertLess(first_uninstall, reboots[0])

    def test_a_vm_that_stops_booting_is_flagged_for_rebuild_and_later_tiers_do_not_run(self):
        vm = FakeVm(self.INSTALLED, bootloop_on_reboot=True)
        with patch.object(svc, "BOOT_TIMEOUT", 0):
            report = self._run(vm, None)
        self.assertTrue(report.needs_rebuild)
        self.assertEqual(len(report.tiers), 1)  # the second tier never ran
        self.assertEqual(report.tiers[0].problems, ["Android did not boot after the change"])
        self.assertIn("must be rebuilt", report.summary())

    def test_an_unhealthy_but_booting_vm_rolls_the_tier_back_and_stops(self):
        vm = FakeVm(self.INSTALLED, sick_after_uninstall=True)
        report = self._run(vm, None)
        first = report.tiers[0]
        self.assertTrue(first.rolled_back)
        self.assertTrue(first.problems)
        self.assertEqual(len(report.tiers), 1)
        self.assertIn("com.google.android.apps.maps", vm.installed)  # put back
        self.assertEqual(report.removed_count, 0)
        self.assertFalse(report.needs_rebuild)

    def test_an_unverified_build_only_gets_the_low_risk_apps_tier(self):
        vm = FakeVm(self.INSTALLED, fingerprint="google/other/build:99/XYZ/1:user/release-keys")
        report = self._run(vm, None)
        self.assertEqual([t.tier for t in report.tiers], ["apps"])
        self.assertIn("com.google.android.googlequicksearchbox", vm.installed)

    def test_a_verified_build_gets_both_tiers(self):
        report = self._run(FakeVm(self.INSTALLED), None)
        self.assertEqual([t.tier for t in report.tiers], ["apps", "services"])

    def test_explicit_tiers_override_the_build_gate(self):
        vm = FakeVm(self.INSTALLED, fingerprint="unknown")
        report = self._run(vm, None, tiers=("services",))
        self.assertEqual([t.tier for t in report.tiers], ["services"])


class RebootTests(unittest.TestCase):
    def test_returns_true_once_android_reports_booted(self):
        vm = FakeVm({"a"})
        self.assertTrue(svc.reboot_and_wait(vm, sleep=lambda _s: None))

    def test_returns_false_on_a_boot_loop(self):
        vm = FakeVm({"a"}, bootloop_on_reboot=True)
        ticks = iter(range(0, 10_000, 50))
        self.assertFalse(svc.reboot_and_wait(vm, timeout=100, sleep=lambda _s: None, clock=lambda: next(ticks)))

    def test_a_hung_wait_for_device_is_a_failure_not_an_exception(self):
        def adb(*args, timeout=30):
            if args[0] == "wait-for-device":
                raise subprocess.TimeoutExpired(args, timeout)
            return done()

        self.assertFalse(svc.reboot_and_wait(adb, sleep=lambda _s: None))


class KeyboardInstallTests(unittest.TestCase):
    class SlowRegisterVm(FakeVm):
        """Ignores `ime set` until it has been asked `misses` times, like a
        keyboard Android hasn't registered yet."""

        def __init__(self, misses):
            super().__init__({"com.android.settings"})
            self.misses = misses

        def __call__(self, *args, timeout=30):
            if args[:2] == ("shell", "ime") and args[2] == "set" and self.misses > 0:
                self.misses -= 1
                return done()
            return super().__call__(*args, timeout=timeout)

    def _apk(self, tmp):
        apk = Path(tmp) / "k.apk"
        apk.write_bytes(b"x")
        return apk

    def test_a_slow_registration_is_retried_until_it_sticks(self):
        vm = self.SlowRegisterVm(misses=2)
        with tempfile.TemporaryDirectory() as tmp:
            ok, detail = svc.install_keyboard(vm, self._apk(tmp), sleep=lambda _s: None)
        self.assertTrue(ok, detail)

    def test_gives_up_with_a_reason_after_the_attempts_run_out(self):
        vm = self.SlowRegisterVm(misses=99)
        with tempfile.TemporaryDirectory() as tmp:
            ok, detail = svc.install_keyboard(vm, self._apk(tmp), sleep=lambda _s: None)
        self.assertFalse(ok)
        self.assertIn("kept", detail)

    def test_a_failed_install_reports_why(self):
        vm = FakeVm({"a"}, keyboard_install_ok=False)
        with tempfile.TemporaryDirectory() as tmp:
            ok, detail = svc.install_keyboard(vm, self._apk(tmp), sleep=lambda _s: None)
        self.assertFalse(ok)
        self.assertIn("INSTALL_FAILED", detail)


class DownloadTests(unittest.TestCase):
    def _pin(self, payload):
        return vt.PinnedApk("p", "1", "https://example.invalid/x.apk", hashlib.sha256(payload).hexdigest(), "0" * 64)

    def test_a_matching_download_is_kept(self):
        payload = b"apk-bytes"

        class Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_, n=-1, _d=[payload]):
                data, _d[0] = _d[0], b""
                return data

        with tempfile.TemporaryDirectory() as tmp, patch.object(svc.urllib.request, "urlopen", return_value=Resp()):
            path = svc.download_pinned(self._pin(payload), Path(tmp) / "k.apk")
            self.assertEqual(path.read_bytes(), payload)

    def test_a_tampered_download_is_discarded(self):
        class Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_, n=-1, _d=[b"evil"]):
                data, _d[0] = _d[0], b""
                return data

        with tempfile.TemporaryDirectory() as tmp, patch.object(svc.urllib.request, "urlopen", return_value=Resp()):
            with self.assertRaises(svc.TrimError):
                svc.download_pinned(self._pin(b"the real one"), Path(tmp) / "k.apk")
            self.assertFalse((Path(tmp) / "k.apk").exists())
            self.assertEqual(list(Path(tmp).glob("*.part")), [])

    def test_an_already_verified_file_is_reused_without_a_network_call(self):
        payload = b"cached"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "k.apk"
            dest.write_bytes(payload)
            with patch.object(svc.urllib.request, "urlopen", side_effect=AssertionError("no network expected")):
                self.assertEqual(svc.download_pinned(self._pin(payload), dest), dest)


class SummaryTests(unittest.TestCase):
    def test_summary_mentions_rollbacks_and_failures(self):
        report = svc.TrimReport(keyboard="installed and set as default", tiers=[
            svc.TierResult("apps", removed=["a", "b"], failed={"c": "x"}),
            svc.TierResult("services", problems=["System UI isn't running"], rolled_back=True),
        ])
        text = report.summary()
        self.assertIn("apps: 2 removed, 1 could not be removed", text)
        self.assertIn("services: rolled back", text)


if __name__ == "__main__":
    unittest.main()
