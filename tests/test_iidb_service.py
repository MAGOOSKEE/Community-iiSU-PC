"""Unit tests for bridge/services/iidb_service.py's non-network logic
(type/slot mapping, filename derivation, install-plan building). The
actual iiDB HTTP calls and MediaBridge install need a real network/device
and are exercised manually, same as the rest of this feature's original
implementation."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from services import iidb_service as svc


class TypeLabelAndMappingTests(unittest.TestCase):
    def test_known_labels(self):
        self.assertEqual(svc.type_label("iisu_boxart"), "iiSU Box Arts")
        self.assertEqual(svc.type_label("soundbite"), "Soundbites")

    def test_unknown_label_falls_back_to_title_case(self):
        self.assertEqual(svc.type_label("weird_category"), "Weird Category")

    def test_install_mapping_numbered_vs_single(self):
        self.assertEqual(svc.install_mapping("hero"), ("hero", True))
        self.assertEqual(svc.install_mapping("icon"), ("home_icon", False))
        self.assertEqual(svc.install_mapping("boxart"), ("icon", False))

    def test_install_mapping_rejects_unknown_type(self):
        with self.assertRaises(svc.IidbServiceError):
            svc.install_mapping("not_a_real_type")


class AssetExtensionTests(unittest.TestCase):
    def test_from_filename(self):
        self.assertEqual(svc.asset_extension({"filename": "cover.PNG"}, "https://x/y"), "png")

    def test_from_url_when_no_filename(self):
        self.assertEqual(svc.asset_extension({}, "https://x/y/art.jpg?token=abc"), "jpg")

    def test_from_mime_type_as_last_resort(self):
        self.assertEqual(svc.asset_extension({"mime_type": "audio/mpeg"}, "https://x/y"), "mp3")

    def test_defaults_to_bin(self):
        self.assertEqual(svc.asset_extension({}, "https://x/y"), "bin")


class WindowsTargetTests(unittest.TestCase):
    def test_no_match_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(svc, "WINDOWS_STUBS_DIR", Path(tmp)):
                with self.assertRaises(svc.IidbServiceError):
                    svc.windows_target("Nonexistent Game")

    def test_exact_match_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "My Game.pcgame").write_text("{}", encoding="utf-8")
            with patch.object(svc, "WINDOWS_STUBS_DIR", Path(tmp)):
                target = svc.windows_target("My Game")
                self.assertEqual(target["display_name"], "My Game")
                self.assertEqual(target["tab_id"], "windows")
                self.assertIn("My%20Game", target["rom_id"].replace("+", "%20") if "+" in target["rom_id"] else target["rom_id"])

    def test_case_insensitive_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "My Game.pcgame").write_text("{}", encoding="utf-8")
            with patch.object(svc, "WINDOWS_STUBS_DIR", Path(tmp)):
                target = svc.windows_target("my game")
                self.assertEqual(target["display_name"], "My Game")

    def test_ambiguous_match_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Two different-cased filenames on a case-sensitive test filesystem
            # would collide on Windows anyway; simulate the ambiguity check
            # directly against the glob result instead of relying on that.
            (Path(tmp) / "My Game.pcgame").write_text("{}", encoding="utf-8")
            with patch.object(svc, "WINDOWS_STUBS_DIR", Path(tmp)), patch.object(
                Path, "glob", return_value=[Path(tmp) / "My Game.pcgame", Path(tmp) / "MY GAME.pcgame"]
            ):
                with self.assertRaises(svc.IidbServiceError):
                    svc.windows_target("My Game")


class BuildInstallPlanTests(unittest.TestCase):
    def _fake_target(self, name):
        return {"tab_id": "windows", "rom_id": f"rom-{name}", "display_name": name, "asset_dir": "/sdcard/x"}

    def test_empty_cart_raises(self):
        with self.assertRaises(svc.IidbServiceError):
            svc.build_install_plan({})

    def test_numbered_slots_increment(self):
        cart = {
            "k1": {"game_name": "Game A", "asset": {"type": "screenshot"}},
            "k2": {"game_name": "Game A", "asset": {"type": "screenshot"}},
        }
        with patch.object(svc, "windows_target", return_value=self._fake_target("Game A")):
            plan = svc.build_install_plan(cart)
        self.assertEqual(plan[0]["slot"], 1)
        self.assertEqual(plan[1]["slot"], 2)
        self.assertEqual(plan[0]["logical_type"], "screenshot")

    def test_duplicate_single_slot_raises(self):
        cart = {
            "k1": {"game_name": "Game A", "asset": {"type": "iisu_boxart"}},
            "k2": {"game_name": "Game A", "asset": {"type": "boxart"}},  # both map to the "icon" slot
        }
        with patch.object(svc, "windows_target", return_value=self._fake_target("Game A")):
            with self.assertRaises(svc.IidbServiceError):
                svc.build_install_plan(cart)

    def test_different_games_dont_collide(self):
        cart = {
            "k1": {"game_name": "Game A", "asset": {"type": "icon"}},
            "k2": {"game_name": "Game B", "asset": {"type": "icon"}},
        }
        targets = {"Game A": self._fake_target("Game A"), "Game B": self._fake_target("Game B")}
        with patch.object(svc, "windows_target", side_effect=lambda name: targets[name]):
            plan = svc.build_install_plan(cart)
        self.assertEqual(len(plan), 2)


class CartKeyTests(unittest.TestCase):
    def test_uses_asset_id_when_present(self):
        self.assertEqual(svc.cart_key(42, {"id": "abc"}), "42|abc")

    def test_falls_back_to_filename(self):
        self.assertEqual(svc.cart_key(42, {"filename": "cover.png"}), "42|cover.png")


if __name__ == "__main__":
    unittest.main()
