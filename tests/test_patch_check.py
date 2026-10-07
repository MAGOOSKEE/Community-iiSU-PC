"""Unit tests for installer/patch_check.py on synthetic decompiled trees (no
apktool, JRE or real APK needed)."""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

import patch_check as pc  # noqa: E402; path set up above
import patch_iisu as pi  # noqa: E402

START_ACTIVITY = "    invoke-virtual {p0, v1}, Landroid/content/Context;->startActivity(Landroid/content/Intent;)V\n"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _coordinator(name="ax2") -> str:
    return f".class public final L{name};\n.method a()V\n    const-string v0, \"GameLaunchCoordinator\"\n{START_ACTIVITY}.end method\n"


def _holder(name="jq6") -> str:
    return f".class public final L{name};\n# instance fields\n.method public constructor <init>()V\n.end method\n{pi.PRIMARY_HOME_ACTIONS_METHOD_ANCHOR}\n.end method\n"


def _helper(name="xa7") -> str:
    body = "\n".join(f"{sig}\n    return-void\n.end method\n" for sig in pi.ASSET_HELPER_METHODS)
    return f".class public final L{name};\n{body}"


def _full_tree(root: Path) -> None:
    _write(root, "smali_classes2/ax2.smali", _coordinator())
    _write(root, "smali_classes2/jq6.smali", _holder())
    _write(root, "smali_classes2/xa7.smali", _helper())
    _write(root, "smali/com/iisulauncher/launcher/MainActivity.smali", f".class public Lx;\n{pi.MAIN_ACTIVITY_REFRESH_METHOD}\n.end method\n")
    _write(root, "AndroidManifest.xml", "<manifest><application></application></manifest>")


def _statuses(root: Path) -> dict[str, str]:
    return {f.name: f.status for f in pc.inspect_decompiled(root)}


class InspectTests(unittest.TestCase):
    def test_complete_tree_is_all_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            _full_tree(Path(tmp))
            statuses = _statuses(Path(tmp))
        self.assertEqual(set(statuses.values()), {pc.OK})
        self.assertEqual(len(statuses), 5)

    def test_missing_launch_hook_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _full_tree(root)
            (root / "smali_classes2" / "ax2.smali").unlink()
            self.assertEqual(_statuses(root)["Game launch hook"], pc.FAIL)

    def test_main_activity_anchor_path_is_recognized_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _full_tree(root)
            (root / "smali_classes2" / "ax2.smali").unlink()
            main = f".class public Lx;\n.method a()V\n    const-string v0, \"{pi.LOG_ANCHOR}\"\n{START_ACTIVITY}{START_ACTIVITY}.end method\n"
            _write(root, "smali/com/iisulauncher/launcher/MainActivity.smali", main + pi.MAIN_ACTIVITY_REFRESH_METHOD + "\n")
            findings = {f.name: f for f in pc.inspect_decompiled(root)}
        self.assertEqual(findings["Game launch hook"].status, pc.OK)
        self.assertIn("2 startActivity", findings["Game launch hook"].detail)

    def test_missing_holder_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _full_tree(root)
            (root / "smali_classes2" / "jq6.smali").unlink()
            self.assertEqual(_statuses(root)["PrimaryHomeActions holder"], pc.FAIL)

    def test_missing_asset_helper_only_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _full_tree(root)
            (root / "smali_classes2" / "xa7.smali").unlink()
            self.assertEqual(_statuses(root)["Media asset-index helper"], pc.DEGRADED)

    def test_missing_refresh_entry_only_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _full_tree(root)
            _write(root, "smali/com/iisulauncher/launcher/MainActivity.smali", ".class public Lx;\n")
            self.assertEqual(_statuses(root)["MainActivity refresh entry point"], pc.DEGRADED)

    def test_manifest_without_application_tag_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _full_tree(root)
            _write(root, "AndroidManifest.xml", "<manifest/>")
            self.assertEqual(_statuses(root)["AndroidManifest.xml"], pc.FAIL)

    def test_inspection_does_not_modify_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _full_tree(root)
            before = {p: p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}
            pc.inspect_decompiled(root)
            after = {p: p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class ReportTests(unittest.TestCase):
    def _report(self, *statuses):
        findings = [pc.Finding(f"check{i}", status, "d") for i, status in enumerate(statuses)]
        return pc.CheckReport("a.apk", "abc", None, "1.2", findings)

    def test_verdicts(self):
        self.assertTrue(self._report(pc.OK, pc.OK).verdict.startswith("SUPPORTED:"))
        self.assertTrue(self._report(pc.OK, pc.DEGRADED).verdict.startswith("SUPPORTED WITH LIMITS"))
        self.assertTrue(self._report(pc.DEGRADED, pc.FAIL).verdict.startswith("NOT SUPPORTED"))

    def test_format_marks_each_finding_and_ends_with_the_verdict(self):
        text = pc.format_report(self._report(pc.OK, pc.FAIL))
        self.assertIn("[OK  ] check0", text)
        self.assertIn("[FAIL] check1", text)
        self.assertEqual(text.splitlines()[-1], self._report(pc.OK, pc.FAIL).verdict)
        self.assertIn("not one this project has seen before", text)

    def test_known_build_is_named(self):
        sha = next(iter(pc.KNOWN_BUILDS))
        report = pc.CheckReport("a.apk", sha, pc.KNOWN_BUILDS[sha], None, [])
        self.assertIn(pc.KNOWN_BUILDS[sha], pc.format_report(report))


class VersionNameTests(unittest.TestCase):
    def test_reads_version_name_from_apktool_yml(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "apktool.yml", "versionInfo:\n  versionCode: 9\n  versionName: 0.1.6.1\nother: x\n")
            self.assertEqual(pc.read_version_name(Path(tmp)), "0.1.6.1")

    def test_quoted_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "apktool.yml", "versionInfo:\n  versionName: '2.0'\n")
            self.assertEqual(pc.read_version_name(Path(tmp)), "2.0")
            self.assertIsNone(pc.read_version_name(Path(tmp) / "nowhere"))

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.bin"
            path.write_bytes(b"abc")
            self.assertEqual(pc.sha256_file(path), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


if __name__ == "__main__":
    unittest.main()
