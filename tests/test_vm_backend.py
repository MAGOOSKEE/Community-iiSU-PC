"""Unit tests for shared/vm_backend.py: backend selection and the pure
parsing helpers the Waydroid backend leans on."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared import vm_backend as vb  # noqa: E402; path set up above


class PreferredBackendTests(unittest.TestCase):
    def test_windows_is_always_avd(self):
        self.assertEqual(vb.preferred_backend({"WAYLAND_DISPLAY": "wayland-0"}, is_windows=True), vb.BACKEND_AVD)

    def test_wayland_session_type_picks_waydroid(self):
        self.assertEqual(vb.preferred_backend({"XDG_SESSION_TYPE": "wayland"}, is_windows=False), vb.BACKEND_WAYDROID)

    def test_wayland_display_alone_picks_waydroid(self):
        self.assertEqual(vb.preferred_backend({"WAYLAND_DISPLAY": "wayland-1"}, is_windows=False), vb.BACKEND_WAYDROID)

    def test_steam_game_mode_counts_as_wayland(self):
        self.assertTrue(vb.is_wayland_session({"XDG_CURRENT_DESKTOP": "gamescope"}, is_windows=False))
        self.assertTrue(vb.is_wayland_session({"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}, is_windows=False))
        self.assertEqual(vb.preferred_backend({"XDG_CURRENT_DESKTOP": "gamescope"}, is_windows=False), vb.BACKEND_WAYDROID)

    def test_x11_picks_avd(self):
        self.assertEqual(vb.preferred_backend({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, is_windows=False), vb.BACKEND_AVD)

    def test_empty_environment_picks_avd(self):
        self.assertEqual(vb.preferred_backend({}, is_windows=False), vb.BACKEND_AVD)

    def test_env_override_forces_avd_on_wayland(self):
        env = {"XDG_SESSION_TYPE": "wayland", vb.ENV_OVERRIDE: "avd"}
        self.assertEqual(vb.preferred_backend(env, is_windows=False), vb.BACKEND_AVD)

    def test_env_override_forces_waydroid_on_x11(self):
        self.assertEqual(vb.preferred_backend({vb.ENV_OVERRIDE: "waydroid"}, is_windows=False), vb.BACKEND_WAYDROID)

    def test_env_override_cannot_force_waydroid_on_windows(self):
        self.assertEqual(vb.preferred_backend({vb.ENV_OVERRIDE: "waydroid"}, is_windows=True), vb.BACKEND_AVD)

    def test_garbage_override_is_ignored(self):
        self.assertEqual(vb.preferred_backend({vb.ENV_OVERRIDE: "bluestacks", "XDG_SESSION_TYPE": "wayland"}, is_windows=False), vb.BACKEND_WAYDROID)


class ResolveBackendTests(unittest.TestCase):
    def test_saved_choice_beats_the_current_session(self):
        env = {"XDG_SESSION_TYPE": "wayland"}
        self.assertEqual(vb.resolve_backend({"vm_backend": "avd"}, env, is_windows=False), vb.BACKEND_AVD)
        self.assertEqual(vb.resolve_backend({"vm_backend": "waydroid"}, {}, is_windows=False), vb.BACKEND_WAYDROID)

    def test_install_from_before_the_setting_existed_stays_avd_even_on_wayland(self):
        env = {"XDG_SESSION_TYPE": "wayland"}
        self.assertEqual(vb.resolve_backend({"avd_name": "iisuwin"}, env, is_windows=False), vb.BACKEND_AVD)

    def test_no_config_falls_back_to_detection(self):
        self.assertEqual(vb.resolve_backend(None, {"XDG_SESSION_TYPE": "wayland"}, is_windows=False), vb.BACKEND_WAYDROID)

    def test_windows_ignores_a_saved_waydroid_value(self):
        self.assertEqual(vb.resolve_backend({"vm_backend": "waydroid"}, {}, is_windows=True), vb.BACKEND_AVD)

    def test_invalid_saved_value_is_ignored(self):
        self.assertEqual(vb.resolve_backend({"vm_backend": "nope"}, {}, is_windows=False), vb.BACKEND_AVD)


class WaydroidInstalledTests(unittest.TestCase):
    def test_uses_which(self):
        self.assertTrue(vb.waydroid_installed(lambda name: "/usr/bin/waydroid"))
        self.assertFalse(vb.waydroid_installed(lambda name: None))


class VmDeviceTests(unittest.TestCase):
    def test_emulator_serial(self):
        out = "List of devices attached\nemulator-5554\tdevice\n\n"
        self.assertEqual(vb.vm_device_serial(out), "emulator-5554")

    def test_waydroid_network_serial(self):
        out = "List of devices attached\n192.168.240.112:5555\tdevice\n"
        self.assertEqual(vb.vm_device_serial(out), "192.168.240.112:5555")

    def test_offline_device_is_not_connected(self):
        self.assertFalse(vb.vm_device_connected("List of devices attached\n192.168.240.112:5555\toffline\n"))
        self.assertFalse(vb.vm_device_connected("List of devices attached\nemulator-5554\tunauthorized\n"))

    def test_usb_phone_is_not_the_vm(self):
        self.assertFalse(vb.vm_device_connected("List of devices attached\nR5CT1234ABC\tdevice\n"))

    def test_phone_does_not_hide_the_vm(self):
        out = "List of devices attached\nR5CT1234ABC\tdevice\n192.168.240.112:5555\tdevice\n"
        self.assertEqual(vb.vm_device_serial(out), "192.168.240.112:5555")

    def test_empty(self):
        self.assertFalse(vb.vm_device_connected(""))
        self.assertFalse(vb.vm_device_connected("List of devices attached\n\n"))


class WaydroidStatusTests(unittest.TestCase):
    def test_parses_key_value_lines(self):
        text = "Session:\tRUNNING\nContainer:\tRUNNING\nVendor type:\tMAINLINE\nWayland display:\twayland-0\n"
        status = vb.parse_waydroid_status(text)
        self.assertEqual(status["session"], "RUNNING")
        self.assertEqual(status["container"], "RUNNING")
        self.assertEqual(status["vendor_type"], "MAINLINE")
        self.assertEqual(status["wayland_display"], "wayland-0")

    def test_stopped_session(self):
        self.assertEqual(vb.parse_waydroid_status("Session:\tSTOPPED\nContainer:\tFROZEN\n")["session"], "STOPPED")

    def test_ignores_noise(self):
        self.assertEqual(vb.parse_waydroid_status("no colons here\n\n"), {})


class AddressCandidateTests(unittest.TestCase):
    def test_default_when_nothing_else_is_known(self):
        self.assertEqual(vb.waydroid_address_candidates(), [vb.WAYDROID_DEFAULT_ADDRESS])

    def test_configured_address_comes_first_and_port_is_stripped(self):
        result = vb.waydroid_address_candidates(configured="10.0.0.9:5555")
        self.assertEqual(result[0], "10.0.0.9")

    def test_newest_lease_first(self):
        leases = "100 aa:bb:cc:dd:ee:01 192.168.240.50 old *\n200 aa:bb:cc:dd:ee:02 192.168.240.77 new *\n"
        result = vb.waydroid_address_candidates(leases_text=leases)
        self.assertEqual(result[:2], ["192.168.240.77", "192.168.240.50"])

    def test_neighbour_table_and_failed_entries(self):
        neigh = "192.168.240.112 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n192.168.240.9  FAILED\n"
        result = vb.waydroid_address_candidates(neigh_text=neigh)
        self.assertIn("192.168.240.112", result)
        self.assertNotIn("192.168.240.9", result)

    def test_no_duplicates(self):
        leases = "1 aa:bb:cc:dd:ee:01 192.168.240.112 x *\n"
        result = vb.waydroid_address_candidates(leases_text=leases, neigh_text="192.168.240.112 lladdr aa REACHABLE\n")
        self.assertEqual(result.count("192.168.240.112"), 1)


def _which_only(*available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


UBUNTU = "NAME=Ubuntu\nID=ubuntu\nID_LIKE=debian\n"
MINT = 'ID=linuxmint\nID_LIKE="ubuntu debian"\n'
FEDORA = "ID=fedora\n"
ARCH = "ID=arch\n"
MANJARO = "ID=manjaro\nID_LIKE=arch\n"
OPENSUSE = 'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n'
ALPINE = "ID=alpine\n"


class InstallPlanTests(unittest.TestCase):
    def test_parse_os_release_strips_quotes_and_comments(self):
        parsed = vb.parse_os_release("# c\nID=\"ubuntu\"\nID_LIKE='debian'\n\nbad line\n")
        self.assertEqual(parsed, {"ID": "ubuntu", "ID_LIKE": "debian"})

    def test_debian_family_uses_apt_noninteractively(self):
        plan = vb.waydroid_install_plan(UBUNTU, _which_only("apt-get"))
        self.assertEqual(plan.manager, "apt")
        self.assertEqual(plan.command[-3:], ("install", "-y", "waydroid"))
        self.assertIn("DEBIAN_FRONTEND=noninteractive", plan.command)

    def test_derivative_matches_through_id_like(self):
        self.assertEqual(vb.waydroid_install_plan(MINT, _which_only("apt-get")).manager, "apt")

    def test_fedora_uses_dnf(self):
        self.assertEqual(vb.waydroid_install_plan(FEDORA, _which_only("dnf")).command, ("dnf", "install", "-y", "waydroid"))

    def test_opensuse_uses_zypper(self):
        self.assertEqual(vb.waydroid_install_plan(OPENSUSE, _which_only("zypper")).manager, "zypper")

    def test_alpine_uses_apk(self):
        self.assertEqual(vb.waydroid_install_plan(ALPINE, _which_only("apk")).command, ("apk", "add", "waydroid"))

    def test_no_plan_when_the_package_manager_is_missing(self):
        self.assertIsNone(vb.waydroid_install_plan(UBUNTU, _which_only()))

    def test_arch_has_no_unattended_plan_but_a_helpful_hint(self):
        self.assertIsNone(vb.waydroid_install_plan(ARCH, _which_only("pacman")))
        self.assertIsNone(vb.waydroid_install_plan(MANJARO, _which_only("pacman")))
        self.assertIn("AUR", vb.waydroid_manual_install_hint(MANJARO))

    def test_unknown_distro_has_no_plan_and_points_at_the_docs(self):
        self.assertIsNone(vb.waydroid_install_plan("ID=nixos\n", _which_only("nix")))
        self.assertIn(vb.WAYDROID_INSTALL_DOCS, vb.waydroid_manual_install_hint("ID=nixos\n"))

    def test_command_is_wrapped_in_pkexec_only_when_elevated(self):
        plan = vb.waydroid_install_plan(FEDORA, _which_only("dnf"))
        self.assertEqual(plan.elevated_command[0], "pkexec")
        self.assertEqual(plan.display, "dnf install -y waydroid")
        self.assertNotIn("pkexec", plan.command)


if __name__ == "__main__":
    unittest.main()
