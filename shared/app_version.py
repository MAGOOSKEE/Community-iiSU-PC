"""The one-line version banner every log this project writes starts with, so
a pasted log or screenshot always says exactly which build produced it
(issue #9 took a guess from traceback line numbers to work that out).

Reads the VERSION file the installer and updater already maintain, and,
when running from a git checkout, appends the branch and commit so two
builds of the same VERSION (dev vs. a release) can be told apart. Never
spawns a subprocess and never raises: a banner is a debugging aid, not
something that may break the thing it's logging.
"""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def read_version(project_root: Path = PROJECT_ROOT) -> str:
    try:
        text = (project_root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return text or "unknown"


def git_state(project_root: Path = PROJECT_ROOT) -> tuple[str | None, str | None]:
    """(branch, short commit) from the .git folder, or (None, None) when
    this isn't a checkout (an installed build has no .git). Detached HEAD
    gives (None, sha). A worktree/submodule .git *file* is not followed."""
    git_dir = project_root / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None, None

    if not head.startswith("ref:"):
        return None, (head[:7] if _SHA_RE.match(head) else None)

    ref = head.split(":", 1)[1].strip()
    branch = ref.removeprefix("refs/heads/")
    sha = None
    try:
        sha = (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        try:
            for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    sha = parts[0]
                    break
        except OSError:
            pass
    return branch, (sha[:7] if sha and _SHA_RE.match(sha) else None)


def version_string(project_root: Path = PROJECT_ROOT) -> str:
    """e.g. "v0.5.3-alpha (dev a3839e9)", or just "v0.5.3-alpha" for an
    installed build."""
    version = read_version(project_root)
    branch, sha = git_state(project_root)
    detail = " ".join(part for part in (branch, sha) if part)
    return f"{version} ({detail})" if detail else version


def banner(component: str, extra: str | None = None, project_root: Path = PROJECT_ROOT) -> str:
    """One line: product, version, which script is logging, Python, OS.
    extra is for anything the caller knows that the others can't (the
    Android backend in use, say)."""
    try:
        python = platform.python_version()
        system = platform.platform()
    except Exception:  # noqa: BLE001; a banner must never break the log it heads
        python, system = sys.version.split()[0], sys.platform
    parts = [f"Community-iiSU-PC {version_string(project_root)}", component, f"Python {python}", system]
    if extra:
        parts.append(extra)
    return " | ".join(parts)
