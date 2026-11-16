"""Every Manager page's "?" (bridge/ui/help_links.py) must lead to a heading
that exists in docs/, by GitHub's anchor rules, so renaming a heading can't
quietly break a page's help."""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from bridge.ui import help_links  # noqa: E402


def github_anchor(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation other than hyphens
    and spaces dropped, spaces to hyphens."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(markdown: str) -> set[str]:
    return {github_anchor(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", markdown, re.MULTILINE)}


class HelpLinkTests(unittest.TestCase):
    def test_anchor_rules(self):
        self.assertEqual(github_anchor("Backup & Restore"), "backup--restore")
        self.assertEqual(github_anchor("Quitting a game: Escape and Select+Start"), "quitting-a-game-escape-and-selectstart")
        self.assertEqual(github_anchor("Seeing and changing the VM's storage"), "seeing-and-changing-the-vms-storage")

    def test_every_topic_names_a_real_heading(self):
        for key, topic in help_links.TOPICS.items():
            file_name, _, anchor = topic.partition("#")
            path = PROJECT_ROOT / "docs" / file_name
            self.assertTrue(path.is_file(), f"{key}: {file_name} doesn't exist")
            self.assertIn(anchor, anchors(path.read_text(encoding="utf-8")), f"{key}: no heading for #{anchor} in {file_name}")

    def test_every_settings_page_has_help(self):
        from bridge.ui.sidebar import NAV_GROUPS

        for _group, subs in NAV_GROUPS.items():
            for sub_key, _label in subs:
                self.assertIn(sub_key, help_links.TOPICS, f"no help topic for {sub_key}")


if __name__ == "__main__":
    unittest.main()
