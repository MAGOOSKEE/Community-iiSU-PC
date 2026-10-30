"""
Where a git checkout stands against GitHub, and what both update paths do with
it: the Updates page check (diagnostics_service.check_for_updates_detailed)
and the apply step (updater.check_and_apply_git_update). A copy that is only
ahead of GitHub is up to date, not "0 new commits available"; one that is
both ahead and behind can't be fast-forwarded and must not offer to. git is
faked, so nothing touches a repository or the network.
"""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import updater  # noqa: E402; path set up above
from services import diagnostics_service  # noqa: E402


def _done(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class FakeGit:
    def __init__(self, ahead: int, behind: int, counts_fail: bool = False):
        self.ahead = ahead
        self.behind = behind
        self.counts_fail = counts_fail
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[:1] == ["rev-parse"] and args[1] == "--abbrev-ref":
            return _done("dev\n")
        if args[:1] == ["fetch"]:
            return _done()
        if args == ["rev-parse", "HEAD"]:
            return _done("local\n")
        if args[:1] == ["rev-parse"]:
            return _done("local\n" if (self.ahead, self.behind) == (0, 0) else "remote\n")
        if args[:2] == ["rev-list", "--left-right"]:
            return _done(returncode=1) if self.counts_fail else _done(f"{self.ahead}\t{self.behind}\n")
        if args[:2] == ["rev-list", "--count"]:
            return _done(f"{self.behind}\n")
        if args[:1] == ["log"]:
            return _done("- a change\n")
        if args[:1] == ["pull"]:
            return _done()
        return _done(returncode=1)

    def pulled(self) -> bool:
        return any(args[:1] == ["pull"] for args in self.calls)


class CommitsAheadBehindTests(unittest.TestCase):
    def test_reads_both_counts(self):
        with patch.object(updater, "_run_git", FakeGit(ahead=9, behind=2)):
            self.assertEqual(updater.commits_ahead_behind("dev"), (9, 2))

    def test_unreadable_is_none(self):
        with patch.object(updater, "_run_git", FakeGit(0, 1, counts_fail=True)):
            self.assertIsNone(updater.commits_ahead_behind("dev"))
        with patch.object(updater, "_run_git", return_value=None):
            self.assertIsNone(updater.commits_ahead_behind("dev"))


class UpdatesPageCheckTests(unittest.TestCase):
    def _check(self, git):
        with patch.object(updater, "is_git_checkout", return_value=True), patch.object(updater, "_run_git", git):
            return diagnostics_service.check_for_updates_detailed()

    def test_same_commit_is_up_to_date(self):
        result = self._check(FakeGit(0, 0))
        self.assertFalse(result.update_available)

    def test_only_ahead_is_up_to_date_not_zero_new_commits(self):
        result = self._check(FakeGit(ahead=9, behind=0))
        self.assertFalse(result.update_available)
        self.assertIn("Up to date", result.message)
        self.assertNotIn("0 new commit", result.message)

    def test_only_behind_offers_the_update(self):
        result = self._check(FakeGit(ahead=0, behind=3))
        self.assertTrue(result.update_available)
        self.assertIn("3 new commit(s)", result.message)

    def test_ahead_and_behind_does_not_offer_an_install_that_cannot_work(self):
        result = self._check(FakeGit(ahead=2, behind=3))
        self.assertFalse(result.update_available)
        self.assertIn("can't fast-forward", result.message)


class ApplyGitUpdateTests(unittest.TestCase):
    def _apply(self, git):
        with patch.object(updater, "_run_git", git), patch("builtins.print"):
            return updater.check_and_apply_git_update()

    def test_only_ahead_pulls_nothing_and_asks_for_no_restart(self):
        git = FakeGit(ahead=9, behind=0)
        message, applied = self._apply(git)
        self.assertFalse(applied)
        self.assertFalse(git.pulled())
        self.assertIn("up to date", message)

    def test_ahead_and_behind_does_not_try_to_pull(self):
        git = FakeGit(ahead=2, behind=3)
        message, applied = self._apply(git)
        self.assertFalse(applied)
        self.assertFalse(git.pulled())
        self.assertIn("can't fast-forward", message)

    def test_only_behind_pulls_and_asks_for_a_restart(self):
        git = FakeGit(ahead=0, behind=3)
        message, applied = self._apply(git)
        self.assertTrue(applied)
        self.assertTrue(git.pulled())


if __name__ == "__main__":
    unittest.main()
