"""Downloads the latest official iiSU release APK from iiSU's own public GitHub
releases (iisu-network/iiSU), straight onto the user's machine. Nothing is
bundled or redistributed by this project; pre-releases are never picked here,
they go through the custom-APK option instead.

Stdlib only so Setup can use it before any dependency is installed.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "iisu-network/iiSU"
API_URL = f"https://api.github.com/repos/{REPO}/releases"
USER_AGENT = "Community-iiSU-PC"


@dataclass
class Release:
    tag: str
    asset_name: str
    download_url: str
    size: int
    sha256: str | None  # from GitHub's asset digest, when it provides one


def fetch_latest_release(timeout: int = 15) -> Release:
    """Newest non-prerelease release that ships an APK (some entries on the
    repo are StarterPack zips or have no assets, those are skipped)."""
    req = urllib.request.Request(API_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        releases = json.loads(resp.read())
    for release in releases:
        if release.get("prerelease") or release.get("draft"):
            continue
        asset = next((a for a in release.get("assets", []) if a["name"].lower().endswith(".apk")), None)
        if asset is None:
            continue
        digest = asset.get("digest") or ""
        return Release(
            tag=release["tag_name"],
            asset_name=asset["name"],
            download_url=asset["browser_download_url"],
            size=int(asset.get("size") or 0),
            sha256=digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None,
        )
    raise RuntimeError(f"No official iiSU release with an APK was found on github.com/{REPO}.")


def download_latest(dest_dir: Path, log=print) -> Path:
    """Downloads the latest official APK into dest_dir and checks it against
    GitHub's published SHA-256. Reuses an already-downloaded identical file."""
    release = fetch_latest_release()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(release.asset_name).name
    if dest.is_file() and release.sha256 and _sha256(dest) == release.sha256:
        log(f"[download] {dest.name} (iiSU {release.tag}) is already downloaded and verified.")
        return dest

    log(f"[download] Fetching {release.asset_name} (iiSU {release.tag}, {release.size / 1e6:.0f} MB) from github.com/{REPO}...")
    tmp = dest.with_suffix(".apk.part")
    req = urllib.request.Request(release.download_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
        if release.sha256 and _sha256(tmp) != release.sha256:
            raise RuntimeError("Downloaded APK doesn't match GitHub's published SHA-256, discarded it. Try again.")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    log(f"[download] Saved {dest}")
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()
