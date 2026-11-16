"""
Checks that the newest iiSU builds still take the patch, so a breaking iiSU
update is found by a scheduled job instead of by someone's Setup failing.

For the newest official release and the newest pre-release on iiSU's own
GitHub releases, it downloads the APK into a temporary folder, runs the same
read-only report as `installer/patch_check.py` (decompiles, finds each hook,
builds nothing), and prints a Markdown summary. The APKs are deleted
afterwards: nothing is kept, committed or redistributed.

Exit code 0 when every build checked takes the patch (a "degraded" media hook
still counts), 1 when a required hook is missing in any of them.

    python scripts/check_latest_iisu.py [--summary FILE] [--skip-known]

--summary appends the Markdown report to FILE (CI passes $GITHUB_STEP_SUMMARY).
--skip-known skips builds whose SHA-256 is already in patch_check.KNOWN_BUILDS.
Used by .github/workflows/iisu-watch.yml.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "installer"))
sys.path.insert(0, str(PROJECT_ROOT))

import iisu_release  # noqa: E402
import patch_check  # noqa: E402


def newest_builds(releases: list[dict]) -> list[dict]:
    """The newest release and the newest pre-release that ship an APK, as
    {"tag", "prerelease", "name", "url", "sha256"}; the pre-release only when
    it's newer than the release (GitHub lists newest first)."""
    picked: list[dict] = []
    seen_kinds: set[bool] = set()
    for release in releases:
        if release.get("draft"):
            continue
        kind = bool(release.get("prerelease"))
        if kind in seen_kinds:
            continue
        asset = next((a for a in release.get("assets", []) if a["name"].lower().endswith(".apk")), None)
        if asset is None:
            continue
        if kind and False in seen_kinds:
            # A pre-release older than the newest release isn't worth checking.
            continue
        digest = asset.get("digest") or ""
        picked.append({
            "tag": release["tag_name"],
            "prerelease": kind,
            "name": asset["name"],
            "url": asset["browser_download_url"],
            "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None,
        })
        seen_kinds.add(kind)
        if seen_kinds == {True, False}:
            break
    return picked


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": iisu_release.USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def report_markdown(build: dict, report) -> str:
    kind = "pre-release" if build["prerelease"] else "release"
    lines = [f"### iiSU {build['tag']} ({kind}): {report.verdict}", "", "| Hook | Result | Detail |", "|---|---|---|"]
    for finding in report.findings:
        lines.append(f"| {finding.name} | {finding.status} | {finding.detail} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--skip-known", action="store_true")
    args = parser.parse_args(argv)

    req = urllib.request.Request(iisu_release.API_URL, headers={"User-Agent": iisu_release.USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        builds = newest_builds(json.loads(resp.read()))
    if not builds:
        print("No iiSU release with an APK found.")
        return 0

    failed = False
    sections = []
    with tempfile.TemporaryDirectory() as tmp:
        for build in builds:
            if args.skip_known and build["sha256"] in patch_check.KNOWN_BUILDS:
                sections.append(f"### iiSU {build['tag']}: already known ({patch_check.KNOWN_BUILDS[build['sha256']]}), skipped\n")
                continue
            apk = Path(tmp) / build["name"]
            print(f"Downloading {build['name']} (iiSU {build['tag']})...")
            download(build["url"], apk)
            if build["sha256"] and sha256(apk) != build["sha256"]:
                raise RuntimeError(f"{build['name']} doesn't match GitHub's published SHA-256")
            report = patch_check.check_apk(apk)
            apk.unlink(missing_ok=True)
            sections.append(report_markdown(build, report))
            failed = failed or report.failed

    text = "## iiSU patch compatibility\n\n" + "\n".join(sections)
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
