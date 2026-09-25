"""
Checks for updates to Community-iiSU-PC itself, run first thing by start_iisu_pc.py
so a normal day-to-day start always at least knows whether it's running
stale code, and, for a git checkout, gets current automatically.

Two install shapes, two different checks:

  - A git checkout (PROJECT_ROOT has a .git folder, the normal case for
    a dev clone, on whichever branch, master or dev): `git fetch` the
    current branch, then a fast-forward-only `git pull` if it's behind.
    No special-casing between dev and master is needed, whatever branch
    is actually checked out is the one this fetches and fast-forwards,
    so a dev checkout updates from dev and a master checkout updates from
    master automatically. --ff-only is what makes this safe to run
    unattended before every single start: it only ever applies a clean,
    already-merged update, and refuses outright (never merges, rebases,
    or clobbers) the moment there's any local commit or edit it would
    otherwise have to reconcile, the worst case is always "did nothing,
    printed why."
  - A plain extracted/zipped install (no .git, a normal end user's
    download): downloads the newer release's tag as a plain zip (GitHub
    auto-generates one per tag/release with no action needed from this
    project, "zipball_url" in the Releases API response) and copies it
    over this install in place. This is safe for exactly the reason it
    looks unsafe at first: that zipball is a snapshot of tracked files
    only, the same thing `git checkout <tag>` would give a git install,
    everything .gitignore excludes (config.json, the portable SDK/AVD
    copy, logs, caches, the setup keystore, downloaded build-tools) is
    physically absent from it, because none of that is ever committed to
    begin with. So copying every file the zipball contains over this
    install, and touching nothing else, can't clobber user/machine state
    without needing a second, hand-maintained exclude list that could
    drift out of sync with .gitignore over time. Nothing already in this
    install is ever deleted, even a file the new release has since
    dropped, an update is additive/overwrite-only, never subtractive.

Best-effort throughout: no internet, GitHub/the remote being unreachable,
or git not being on PATH all skip silently (well, loudly to the log, but
never fatally), this is a nice-to-have layered on top of starting
Community-iiSU-PC, never a prerequisite for it. A successful pull/update also can't
take effect in the process that just applied it (Python already loaded
the old files into memory), it always says so, since the honest fix is
"restart Community-iiSU-PC," not pretending to hot-swap running code. Likewise, if
any file couldn't be overwritten (Windows keeping something open is the
realistic case, an in-use build-tools binary, say), VERSION is left
unbumped on purpose, so an incomplete update is retried in full next
launch rather than being reported as done when it wasn't.
"""

import json
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
GIT_DIR = PROJECT_ROOT / ".git"
VERSION_PATH = PROJECT_ROOT / "VERSION"
GITHUB_REPO = "MAGOOSKEE/Community-iiSU-PC"
GIT_TIMEOUT = 10.0
HTTP_TIMEOUT = 5.0
DOWNLOAD_TIMEOUT = 30.0


def _run_git(args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=GIT_TIMEOUT,
            creationflags=0x08000000,  # CREATE_NO_WINDOW, git.exe is console-subsystem
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def is_git_checkout() -> bool:
    return GIT_DIR.is_dir()


def current_branch() -> str | None:
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if result is None or result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch if branch and branch != "HEAD" else None


def check_and_apply_git_update() -> None:
    branch = current_branch()
    if branch is None:
        print("[updater] git checkout isn't on a branch (detached HEAD?), skipping the update check")
        return

    print(f"[updater] checking for updates ({branch})...")
    fetch = _run_git(["fetch", "origin", branch])
    if fetch is None or fetch.returncode != 0:
        reason = fetch.stderr.strip()[:200] if fetch else "git not found or fetch timed out"
        print(f"[updater] couldn't reach GitHub to check for updates, continuing with what's here ({reason})")
        return

    local = _run_git(["rev-parse", "HEAD"])
    remote = _run_git(["rev-parse", f"origin/{branch}"])
    local_sha = local.stdout.strip() if local and local.returncode == 0 else None
    remote_sha = remote.stdout.strip() if remote and remote.returncode == 0 else None
    if not local_sha or not remote_sha:
        print("[updater] couldn't compare local/remote commits, skipping")
        return
    if local_sha == remote_sha:
        print(f"[updater] already up to date ({branch}).")
        return

    count = _run_git(["rev-list", "--count", f"HEAD..origin/{branch}"])
    behind = count.stdout.strip() if count and count.returncode == 0 else "some"
    print(f"[updater] {behind} new commit(s) on {branch}, pulling...")

    pull = _run_git(["pull", "--ff-only", "origin", branch])
    if pull is not None and pull.returncode == 0:
        print(f"[updater] updated to the latest {branch}. This run is still using the old code, restart Community-iiSU-PC to pick it up.")
    else:
        reason = pull.stderr.strip()[:300] if pull else "git not found or pull timed out"
        print(
            f"[updater] {behind} update(s) available on {branch}, but couldn't fast-forward automatically "
            f"(likely local changes here), update manually with `git pull` when convenient:\n[updater]   {reason}"
        )


def _copy_release_tree(src: Path, dst: Path) -> list[str]:
    """Copies every file under src into dst, overwriting what's there and
    creating whatever's new, never deleting anything in dst that isn't
    in src (see module docstring for why that's the safe, sufficient
    boundary here). Returns a list of "relpath: error" strings for any
    file that couldn't be written, e.g. Windows holding it open."""
    errors = []
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        except OSError as e:
            errors.append(f"{rel}: {e}")
    return errors


def check_release_update() -> None:
    current = VERSION_PATH.read_text(encoding="utf-8").strip() if VERSION_PATH.is_file() else None
    try:
        # Not /releases/latest: that endpoint only ever considers
        # non-prerelease releases, and this project's releases are all
        # tagged --prerelease during alpha, it would 404 forever
        # otherwise (confirmed live). /releases lists every release,
        # newest first, prerelease or not, so [0] is the one actually
        # meant to be "latest" for this project right now.
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases",
            headers={"User-Agent": "Community-iiSU-PC", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            releases = json.loads(resp.read())
        if not releases:
            print("[updater] no releases published yet, skipping")
            return
        release = releases[0]
        latest = release["tag_name"]
        zip_url = release["zipball_url"]
    except Exception as e:
        print(f"[updater] couldn't check for updates ({e}), continuing")
        return

    if current == latest:
        print(f"[updater] already on the latest release ({current}).")
        return
    if current is None:
        print(
            f"[updater] latest release is {latest} (this install doesn't have a VERSION file to "
            f"compare against), skipping auto-update; grab it manually: "
            f"https://github.com/{GITHUB_REPO}/releases/latest"
        )
        return

    print(f"[updater] a newer release is available: {latest} (this install is {current}), downloading...")

    staging_dir = PROJECT_ROOT / "_update_staging"
    try:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)

        zip_path = staging_dir / "release.zip"
        zip_req = urllib.request.Request(zip_url, headers={"User-Agent": "Community-iiSU-PC"})
        with urllib.request.urlopen(zip_req, timeout=DOWNLOAD_TIMEOUT) as resp, open(zip_path, "wb") as f:
            shutil.copyfileobj(resp, f)

        extract_dir = staging_dir / "extracted"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        # GitHub wraps every auto-generated zipball in one top-level
        # <repo>-<sha-or-tag>/ folder, found by position, not name,
        # since that naming scheme isn't something this project controls
        # or should assume stays fixed.
        top_level = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(top_level) != 1:
            print(f"[updater] unexpected archive layout ({len(top_level)} top-level folder(s)), aborting, nothing changed")
            return
        release_root = top_level[0]

        errors = _copy_release_tree(release_root, PROJECT_ROOT)
        if errors:
            print(f"[updater] update incomplete, {len(errors)} file(s) couldn't be written, will retry next launch:")
            for line in errors[:10]:
                print(f"[updater]   {line}")
            return

        VERSION_PATH.write_text(latest + "\n", encoding="utf-8")
        print(f"[updater] updated to {latest}. This run is still using the old code, restart Community-iiSU-PC to pick it up.")
    except Exception as e:
        print(f"[updater] update download/apply failed ({e}), this install is unchanged, still on {current}")
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def check_for_updates() -> None:
    """Never raises, called unconditionally at the top of every
    start_iisu_pc.py run, and a broken update check is never a good
    reason to fail an otherwise-normal start."""
    try:
        if is_git_checkout():
            check_and_apply_git_update()
        else:
            check_release_update()
    except Exception as e:
        print(f"[updater] update check failed ({e}), continuing")
