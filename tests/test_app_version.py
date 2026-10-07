"""Unit tests for shared/app_version.py's version/git lookup and banner."""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared import app_version as av  # noqa: E402; path set up above

SHA = "a3839e9f1c2d4b5e6f708192a3b4c5d6e7f80912"


def _checkout(root: Path, head: str, refs: dict[str, str] | None = None, packed: str | None = None) -> None:
    git = root / ".git"
    git.mkdir()
    (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    for ref, sha in (refs or {}).items():
        path = git / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha + "\n", encoding="utf-8")
    if packed is not None:
        (git / "packed-refs").write_text(packed, encoding="utf-8")


class ReadVersionTests(unittest.TestCase):
    def test_reads_and_strips_the_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "VERSION").write_text("v0.5.3-alpha\n", encoding="utf-8")
            self.assertEqual(av.read_version(Path(tmp)), "v0.5.3-alpha")

    def test_missing_or_empty_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(av.read_version(Path(tmp)), "unknown")
            (Path(tmp) / "VERSION").write_text("  \n", encoding="utf-8")
            self.assertEqual(av.read_version(Path(tmp)), "unknown")


class GitStateTests(unittest.TestCase):
    def test_branch_and_short_sha_from_a_loose_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            _checkout(Path(tmp), "ref: refs/heads/dev", {"refs/heads/dev": SHA})
            self.assertEqual(av.git_state(Path(tmp)), ("dev", "a3839e9"))

    def test_branch_name_with_a_slash(self):
        with tempfile.TemporaryDirectory() as tmp:
            _checkout(Path(tmp), "ref: refs/heads/feature/x", {"refs/heads/feature/x": SHA})
            self.assertEqual(av.git_state(Path(tmp))[0], "feature/x")

    def test_falls_back_to_packed_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            packed = f"# pack-refs with: peeled\n{SHA} refs/heads/master\n"
            _checkout(Path(tmp), "ref: refs/heads/master", packed=packed)
            self.assertEqual(av.git_state(Path(tmp)), ("master", "a3839e9"))

    def test_detached_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            _checkout(Path(tmp), SHA)
            self.assertEqual(av.git_state(Path(tmp)), (None, "a3839e9"))

    def test_unresolvable_ref_keeps_the_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            _checkout(Path(tmp), "ref: refs/heads/dev")
            self.assertEqual(av.git_state(Path(tmp)), ("dev", None))

    def test_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(av.git_state(Path(tmp)), (None, None))


class BannerTests(unittest.TestCase):
    def test_installed_build_has_no_git_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "VERSION").write_text("v0.5.3-alpha\n", encoding="utf-8")
            self.assertEqual(av.version_string(Path(tmp)), "v0.5.3-alpha")

    def test_checkout_shows_branch_and_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "VERSION").write_text("v0.5.3-alpha\n", encoding="utf-8")
            _checkout(Path(tmp), "ref: refs/heads/dev", {"refs/heads/dev": SHA})
            self.assertEqual(av.version_string(Path(tmp)), "v0.5.3-alpha (dev a3839e9)")

    def test_banner_is_one_line_with_every_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "VERSION").write_text("v0.5.3-alpha\n", encoding="utf-8")
            line = av.banner("setup", "backend avd", Path(tmp))
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith("Community-iiSU-PC v0.5.3-alpha | setup | Python "))
        self.assertTrue(line.endswith("| backend avd"))

    def test_never_raises_even_with_no_project_files(self):
        self.assertIn("unknown", av.banner("x", project_root=Path("Z:/definitely/not/here")))


if __name__ == "__main__":
    unittest.main()
