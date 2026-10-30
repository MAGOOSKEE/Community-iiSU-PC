"""Unit tests for installer/patch_iisu.py's structural class discovery and
MediaBridge token substitution (pure text work on synthetic smali, no
apktool/JRE/SDK involved)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

import patch_iisu  # noqa: E402; path set up above


def _helper_smali(class_name: str, signatures=patch_iisu.ASSET_HELPER_METHODS) -> str:
    body = "\n".join(f"{sig}\n    return-void\n.end method\n" for sig in signatures)
    return f".class public final L{class_name};\n.super Ljava/lang/Object;\n\n{body}"


class RedirectStartActivityTests(unittest.TestCase):
    """The coordinator's dispatch method, as iiSU 7.5-prerelease2 compiles it
    (class ax2, method k()): one call passes an options Bundle, one doesn't."""

    COORDINATOR = (
        ".class public final Lax2;\n"
        ".method public final k()V\n"
        '    const-string v4, "GameLaunchCoordinator"\n'
        "    if-eqz v0, :cond_5\n"
        "    invoke-virtual {v1, p0, v0}, Landroid/content/Context;->startActivity(Landroid/content/Intent;Landroid/os/Bundle;)V\n"
        "    return-void\n"
        "    :cond_5\n"
        "    invoke-virtual {v1, p0}, Landroid/content/Context;->startActivity(Landroid/content/Intent;)V\n"
        "    return-void\n"
        ".end method\n"
    )

    def test_the_options_bundle_goes_along_when_there_was_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ax2.smali"
            path.write_text(self.COORDINATOR, encoding="utf-8")
            self.assertEqual(patch_iisu.patch_all_start_activity_calls(path), 2)
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("->startActivity(", text)
        self.assertIn(
            "    invoke-static {v1, p0, v0}, Lcom/iisulauncher/pcbridge/LaunchBridge;->launch("
            "Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V\n", text,
        )
        self.assertIn(
            "    invoke-static {v1, p0}, Lcom/iisulauncher/pcbridge/LaunchBridge;->launch("
            "Landroid/content/Context;Landroid/content/Intent;)V\n", text,
        )

    def test_both_launch_forms_exist_in_the_injected_class(self):
        smali = (patch_iisu.SMALI_PATCH_DIR / "LaunchBridge.smali").read_text(encoding="utf-8")
        for signature in ("launch(Landroid/content/Context;Landroid/content/Intent;)V",
                          "launch(Landroid/content/Context;Landroid/content/Intent;Landroid/os/Bundle;)V"):
            with self.subTest(signature=signature):
                self.assertIn(f".method public static {signature}", smali)


class FindAssetHelperTests(unittest.TestCase):
    def _tree(self, tmp: str, files: dict[str, str]) -> Path:
        root = Path(tmp)
        for name, text in files.items():
            (root / name).write_text(text, encoding="utf-8")
        return root

    def test_finds_the_single_class_with_every_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, {"xa7.smali": _helper_smali("xa7"), "other.smali": ".class public final Lother;\n"})
            self.assertEqual(patch_iisu.find_asset_helper_class(root), "xa7")

    def test_partial_signature_set_is_not_a_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, {"half.smali": _helper_smali("half", patch_iisu.ASSET_HELPER_METHODS[:3])})
            self.assertIsNone(patch_iisu.find_asset_helper_class(root))

    def test_ambiguous_match_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, {"a.smali": _helper_smali("a"), "b.smali": _helper_smali("b")})
            self.assertIsNone(patch_iisu.find_asset_helper_class(root))


class InjectLaunchBridgeTests(unittest.TestCase):
    RECEIVER = (
        ".class public final Lcom/iisulauncher/pcbridge/MediaBridgeReceiver;\n"
        "    sget-object v0, LIISUPC_HOLDER;->iisupcHolder:LIISUPC_HOLDER;\n"
        "    # IISUPC_ASSET_INDEX_BEGIN\n"
        "    invoke-static {p3, v9}, LIISUPC_ASSETS;->n(Ljava/io/File;Ljava/lang/String;)Ljava/io/File;\n"
        "    # IISUPC_ASSET_INDEX_END\n"
        "    return-void\n"
    )

    def _inject(self, asset_helper):
        with tempfile.TemporaryDirectory() as tmp:
            patch_dir = Path(tmp) / "smali_patch"
            patch_dir.mkdir()
            (patch_dir / "MediaBridgeReceiver.smali").write_text(self.RECEIVER, encoding="utf-8")
            out = Path(tmp) / "decompiled"
            with patch.object(patch_iisu, "SMALI_PATCH_DIR", patch_dir):
                patch_iisu.inject_launch_bridge(out, "jq6", asset_helper)
            return (out / "smali" / patch_iisu.BRIDGE_PACKAGE_SMALI_DIR / "MediaBridgeReceiver.smali").read_text(encoding="utf-8")

    def test_tokens_are_replaced_with_discovered_classes(self):
        text = self._inject("xa7")
        self.assertIn("Ljq6;->iisupcHolder:Ljq6;", text)
        self.assertIn("Lxa7;->n(", text)
        self.assertNotIn("LIISUPC_", text)

    def test_missing_asset_helper_strips_the_index_block(self):
        text = self._inject(None)
        self.assertIn("Ljq6;->iisupcHolder", text)
        self.assertNotIn("IISUPC_ASSET", text)
        self.assertNotIn("->n(", text)
        self.assertIn("return-void", text)


class MainActivityRefreshTests(unittest.TestCase):
    def test_detects_the_refresh_entry_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "smali" / "com" / "iisulauncher" / "launcher"
            pkg.mkdir(parents=True)
            (pkg / "MainActivity.smali").write_text(patch_iisu.MAIN_ACTIVITY_REFRESH_METHOD + "\n", encoding="utf-8")
            self.assertTrue(patch_iisu.has_main_activity_refresh(Path(tmp)))

    def test_missing_entry_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "MainActivity.smali").write_text(".class public Lx;\n", encoding="utf-8")
            self.assertFalse(patch_iisu.has_main_activity_refresh(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
