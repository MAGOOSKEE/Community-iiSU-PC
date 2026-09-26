"""Non-UI logic behind the "iiSU App Updates" section of the Diagnostics
page: checks iiSU's own public GitHub releases (iisu-network/iiSU) for a
newer official build, and re-patches/reinstalls iiSU from either that
release's APK or a manually-supplied one, the escape hatch for a build
shared outside the normal release feed (e.g. an official pre-release
handed out early on Discord before it's tagged there at all).

iiSU is a third-party, closed-source app this project has no channel into
beyond that one public releases page: there's no other API for "the latest
version," and nothing not published there can be auto-downloaded, only
pointed at manually.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BRIDGE_DIR.parent
IISU_PACKAGE = "com.iisulauncher"
IISU_GITHUB_REPO = "iisu-network/iiSU"
HTTP_TIMEOUT = 15


def _adb_path() -> str:
    bundled_adb = BRIDGE_DIR / "android-sdk-portable" / "sdk" / "platform-tools" / "adb.exe"
    return str(bundled_adb) if bundled_adb.is_file() else "adb"


def _adb_command(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    flags = 0x08000000 if os.name == "nt" else 0
    return subprocess.run(
        [_adb_path(), *args], timeout=timeout, creationflags=flags,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def get_installed_iisu_version() -> str | None:
    """The version currently patched onto the AVD, read live from the
    device rather than trusted from any local record, so it stays correct
    even if a previous update happened outside this app entirely (or the
    AVD was rebuilt from a different source APK). None if the AVD isn't
    reachable or iiSU isn't installed on it, either is a normal state
    (e.g. the AVD is simply stopped right now), not an error."""
    try:
        result = _adb_command("shell", "dumpsys", "package", IISU_PACKAGE)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"versionName=(\S+)", result.stdout)
    return match.group(1) if match else None


@dataclass
class IisuReleaseInfo:
    version: str
    download_url: str
    asset_name: str
    release_url: str


def _fetch_latest_official_release() -> IisuReleaseInfo | None:
    """The most recent non-prerelease release that actually ships an APK
    (a couple of legacy/unrelated releases on this repo have no assets at
    all, and some ship a StarterPack .zip instead), skipping over those
    rather than assuming the newest release entry is always the right
    kind."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{IISU_GITHUB_REPO}/releases",
        headers={"User-Agent": "Community-iiSU-PC", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        releases = json.loads(resp.read())
    for release in releases:
        if release.get("prerelease"):
            continue
        apk_asset = next((a for a in release.get("assets", []) if a["name"].lower().endswith(".apk")), None)
        if apk_asset is None:
            continue
        return IisuReleaseInfo(
            version=release["tag_name"],
            download_url=apk_asset["browser_download_url"],
            asset_name=apk_asset["name"],
            release_url=release["html_url"],
        )
    return None


@dataclass
class IisuUpdateCheck:
    installed_version: str | None
    latest: IisuReleaseInfo | None
    message: str
    update_available: bool


def check_for_iisu_update() -> IisuUpdateCheck:
    """Read-only: never downloads or installs anything.

    iiSU's own version scheme isn't guaranteed comparable across every
    historical tag the way strict semver would be, so this reports a
    *difference* between installed and latest rather than confidently
    asserting "newer"/"older" beyond the plain case of the two strings
    matching."""
    installed = get_installed_iisu_version()
    try:
        latest = _fetch_latest_official_release()
    except Exception as exc:
        return IisuUpdateCheck(installed, None, f"Couldn't reach GitHub to check iiSU's releases: {exc}", False)

    if latest is None:
        return IisuUpdateCheck(installed, None, "No official iiSU release with an APK is published yet.", False)

    if installed is None:
        return IisuUpdateCheck(
            installed,
            latest,
            f"Latest official iiSU release: {latest.version}. Couldn't read the currently-installed "
            "version (the AVD may be stopped), start it once and check again to compare.",
            False,
        )

    if installed.strip().lower() == latest.version.strip().lstrip("vV").lower():
        return IisuUpdateCheck(installed, latest, f"Up to date (iiSU {installed}). Nothing was downloaded or installed.", False)

    return IisuUpdateCheck(
        installed,
        latest,
        f"iiSU update available: {latest.version} (installed: {installed}). Nothing was downloaded or installed yet.",
        True,
    )


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Community-iiSU-PC"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)


def apply_iisu_update(source: str | Path, on_stage=None) -> str:
    """Re-patches and reinstalls iiSU from source: a GitHub release
    download URL (str, the normal "Update Now" path) or a local APK path
    (Path, the manual-override path for a build not on the release feed
    at all). Meant to run on a background thread (see IisuUpdateDialog),
    this can take a few minutes: it decompiles, patches, rebuilds, and
    signs the APK, then boots the AVD once to install the result.

    on_stage(label, index, total), if given, is called at the start of
    each step, forwarded straight through to setup_wizard.update_iisu()
    for its own patch/install steps; the download step here (only for the
    URL case) reports itself the same way first, as its own single-step
    "phase" ahead of update_iisu()'s numbering, since how many steps that
    involves isn't this function's concern."""
    sys.path.insert(0, str(PROJECT_ROOT / "installer"))
    import setup_wizard

    work_dir = PROJECT_ROOT / "installer" / "_work"
    if isinstance(source, str):
        if on_stage:
            on_stage("Downloading the APK", 1, 1)
        work_dir.mkdir(parents=True, exist_ok=True)
        apk_path = work_dir / "iisu_update_download.apk"
        _download(source, apk_path)
    else:
        apk_path = Path(source)

    setup_wizard.update_iisu(apk_path, on_stage=on_stage)
    return f"iiSU updated from {apk_path.name}. Start iiSU to use it."
