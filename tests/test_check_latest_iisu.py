"""scripts/check_latest_iisu.py: which iiSU builds the weekly compatibility
check picks from iiSU's release list. No download, no network."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_latest_iisu as check  # noqa: E402


def release(tag, prerelease=False, apk=True, draft=False):
    assets = [{"name": f"iiSU-{tag}.apk", "browser_download_url": f"https://x/{tag}.apk", "digest": "sha256:ABC"}] if apk else []
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "assets": assets}


class NewestBuildsTests(unittest.TestCase):
    def test_newer_prerelease_and_release(self):
        builds = check.newest_builds([release("8.0-pre", True), release("7.5"), release("7.4")])
        self.assertEqual([b["tag"] for b in builds], ["8.0-pre", "7.5"])
        self.assertEqual(builds[0]["sha256"], "abc")

    def test_older_prerelease_is_skipped(self):
        builds = check.newest_builds([release("7.5"), release("7.5-pre2", True)])
        self.assertEqual([b["tag"] for b in builds], ["7.5"])

    def test_releases_without_an_apk_and_drafts_are_skipped(self):
        builds = check.newest_builds([release("zip", apk=False), release("draft", draft=True), release("7.4")])
        self.assertEqual([b["tag"] for b in builds], ["7.4"])


if __name__ == "__main__":
    unittest.main()
