"""Unit tests for shared/vm_trim.py."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared import vm_trim as vt  # noqa: E402; path set up above


class TierGuardTests(unittest.TestCase):
    def test_no_tier_member_is_protected(self):
        for tier, packages in vt.TIERS.items():
            for package in packages:
                self.assertFalse(vt.is_protected(package), f"{package} in tier {tier!r} is protected")

    def test_tiers_do_not_overlap_and_have_no_duplicates(self):
        everything = [p for packages in vt.TIERS.values() for p in packages]
        self.assertEqual(len(everything), len(set(everything)))

    def test_the_things_iisu_needs_are_kept(self):
        for package in (
            "com.google.android.documentsui",  # the folder picker
            "com.android.externalstorage",
            "com.android.chrome",
            "com.android.vending",
            "com.google.android.gms",
            "com.google.android.gsf",
            "com.google.android.webview",
            "com.iisulauncher",
            vt.LEAN_KEYBOARD_PACKAGE,
        ):
            self.assertTrue(vt.is_protected(package), package)
            for packages in vt.TIERS.values():
                self.assertNotIn(package, packages)

    def test_overlays_and_resource_packages_are_protected(self):
        for package in (
            "com.android.systemui.emulation.pixel_9",
            "com.android.phone.auto_generated_rro_product__",
            "com.google.android.overlay.googleconfig",
            "com.google.android.connectivity.resources",
            "com.android.internal.systemui.navbar.gestural",
            "com.google.mainline.telemetry",
            "com.android.internal.display.cutout.emulation.tall",
        ):
            self.assertTrue(vt.is_protected(package), package)

    def test_gboard_is_not_in_any_tier(self):
        for packages in vt.TIERS.values():
            self.assertNotIn(vt.GBOARD, packages)


class PlanTests(unittest.TestCase):
    INSTALLED = {
        "com.google.android.apps.maps", "com.google.android.googlequicksearchbox", vt.GBOARD,
        "com.android.chrome", "com.android.vending", "com.iisulauncher", "com.not.in.any.tier",
    }

    def test_plan_only_contains_installed_tier_members(self):
        plan = vt.removal_plan(self.INSTALLED)
        self.assertEqual(plan, ["com.google.android.apps.maps", "com.google.android.googlequicksearchbox"])

    def test_gboard_waits_for_the_replacement_keyboard(self):
        self.assertNotIn(vt.GBOARD, vt.removal_plan(self.INSTALLED))
        self.assertIn(vt.GBOARD, vt.removal_plan(self.INSTALLED, keyboard_ready=True))

    def test_tiers_can_be_chosen(self):
        self.assertEqual(vt.removal_plan(self.INSTALLED, tiers=("apps",)), ["com.google.android.apps.maps"])
        self.assertEqual(vt.removal_plan(self.INSTALLED, tiers=("services",)), ["com.google.android.googlequicksearchbox"])

    def test_nothing_installed_means_nothing_to_do(self):
        self.assertEqual(vt.removal_plan(set()), [])

    def test_stock_image_without_google_apps_is_a_no_op_for_google_entries(self):
        self.assertEqual(vt.removal_plan({"com.android.chrome", "com.android.settings"}), [])

    def test_plan_is_stable_and_has_no_duplicates(self):
        installed = {p for packages in vt.TIERS.values() for p in packages} | {vt.GBOARD}
        plan = vt.removal_plan(installed, keyboard_ready=True)
        self.assertEqual(plan, vt.removal_plan(installed, keyboard_ready=True))
        self.assertEqual(len(plan), len(set(plan)))

    def test_protected_packages_are_filtered_even_if_someone_adds_one_to_a_tier(self):
        original = vt.TIERS["apps"]
        try:
            vt.TIERS["apps"] = original + ("com.google.android.gms",)
            self.assertNotIn("com.google.android.gms", vt.removal_plan({"com.google.android.gms"}))
        finally:
            vt.TIERS["apps"] = original


class ParsingTests(unittest.TestCase):
    def test_parse_package_list(self):
        out = "package:com.a\r\npackage:com.b\n\nnoise\n"
        self.assertEqual(vt.parse_package_list(out), {"com.a", "com.b"})

    def test_success_detection(self):
        self.assertTrue(vt.is_success("Success\n"))
        self.assertFalse(vt.is_success("Failure [DELETE_FAILED_INTERNAL_ERROR]"))

    def test_restore_success_is_not_the_word_success(self):
        self.assertTrue(vt.restore_ok("Package com.x installed for user: 0"))
        self.assertFalse(vt.restore_ok("Failure [not installed for 0]"))
        self.assertFalse(vt.restore_ok("Success"))

    def test_command_shapes(self):
        self.assertEqual(vt.uninstall_args("com.x"), ["pm", "uninstall", "--user", "0", "com.x"])
        self.assertEqual(vt.restore_args("com.x"), ["cmd", "package", "install-existing", "--user", "0", "com.x"])
        self.assertEqual(vt.ime_commands()[0][:2], ["ime", "enable"])
        self.assertEqual(vt.ime_commands()[1][:2], ["ime", "set"])


class PinTests(unittest.TestCase):
    def test_keyboard_pin_is_well_formed(self):
        pin = vt.LEAN_KEYBOARD
        self.assertEqual(pin.package, vt.LEAN_KEYBOARD_PACKAGE)
        self.assertTrue(pin.url.startswith("https://github.com/yuliskov/"))
        self.assertRegex(pin.sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(pin.signer_sha256, r"^[0-9a-f]{64}$")
        self.assertTrue(vt.LEAN_KEYBOARD_IME.startswith(vt.LEAN_KEYBOARD_PACKAGE + "/"))


if __name__ == "__main__":
    unittest.main()
