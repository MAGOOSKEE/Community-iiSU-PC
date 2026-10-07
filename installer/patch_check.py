"""Compatibility report for an iiSU APK, without building or installing
anything: decompiles it, then reports whether each hook the patch relies on
can be found, so a new pre-release can be judged in a minute instead of by
finding out partway through Setup.

    python installer/patch_check.py path/to/iiSU.apk

Exit code 0 means the patch should work (possibly with degraded media
features), 1 means a required hook is missing and patch_iisu.py needs
updating for this build.

Everything here is read-only against the decompiled tree; the same finders
patch_iisu.py uses do the looking, so the report can't disagree with what
the real patch would do.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import patch_iisu as pi  # noqa: E402; path set up above

OK, DEGRADED, FAIL = "ok", "degraded", "fail"

# SHA-256 of APKs this project has seen, so the report can say "this exact
# build" instead of guessing from versionName (7.5-prerelease1 and
# prerelease-2 both report 0.1.6.1). Presence means the build was inspected,
# not that every feature was exercised on a device; notes say what was done.
KNOWN_BUILDS: dict[str, str] = {
    "0e9008b2b66c48f98edb7cfa42b3dbde2185ea439179ac673ed2e360769de6e8": "iiSU Alpha 7.4",
    "9c8dcd4349ea0d39ef578b06c23857722c9e23f0922ee452231878cd5916a4cf": "iiSU Alpha 7.5-prerelease1",
    "b8374b68bd54d43fd0e055e8831714155a286b3cd6ad2fa86342d9a24b678ace": "iiSU Alpha 7.5-prerelease-2",
}


@dataclass
class Finding:
    name: str
    status: str
    detail: str


@dataclass
class CheckReport:
    apk_name: str
    sha256: str
    build: str | None
    version_name: str | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(f.status == FAIL for f in self.findings)

    @property
    def degraded(self) -> bool:
        return any(f.status == DEGRADED for f in self.findings)

    @property
    def verdict(self) -> str:
        if self.failed:
            return "NOT SUPPORTED: a required hook is missing, patch_iisu.py needs updating for this build."
        if self.degraded:
            return "SUPPORTED WITH LIMITS: game launching works, some media features will be reduced."
        return "SUPPORTED: every hook was found."


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_version_name(decompiled_dir: Path) -> str | None:
    """versionName out of apktool's own apktool.yml."""
    try:
        text = (decompiled_dir / "apktool.yml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^\s*versionName:\s*'?([^'\r\n]+?)'?\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _launch_hook(decompiled_dir: Path) -> Finding:
    try:
        path = pi.find_main_activity_smali(decompiled_dir)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        anchor = next(i for i, line in enumerate(lines) if pi.LOG_ANCHOR in line)
        start, end = pi.find_enclosing_method(lines, anchor)
        count = sum(1 for line in lines[start:end] if pi.STARTACTIVITY_RE.match(line))
        if count:
            return Finding("Game launch hook", OK, f"MainActivity, {count} startActivity call(s) to redirect")
    except (RuntimeError, StopIteration):
        pass
    try:
        path = pi.find_game_launch_coordinator_smali(decompiled_dir)
        count = sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if pi.STARTACTIVITY_RE.match(line))
        return Finding("Game launch hook", OK, f"GameLaunchCoordinator class {path.stem}, {count} startActivity call(s) to redirect")
    except RuntimeError as exc:
        return Finding("Game launch hook", FAIL, str(exc))


def _holder(decompiled_dir: Path) -> Finding:
    try:
        path, class_name, _text = pi.find_primary_home_actions_holder(decompiled_dir)
        return Finding("PrimaryHomeActions holder", OK, f"class {class_name} ({path.name})")
    except RuntimeError as exc:
        return Finding("PrimaryHomeActions holder", FAIL, str(exc))


def _asset_helper(decompiled_dir: Path) -> Finding:
    name = pi.find_asset_helper_class(decompiled_dir)
    if name:
        return Finding("Media asset-index helper", OK, f"class {name}")
    return Finding("Media asset-index helper", DEGRADED, "not found: media installs will work but won't be re-indexed until iiSU rescans")


def _refresh_entry(decompiled_dir: Path) -> Finding:
    if pi.has_main_activity_refresh(decompiled_dir):
        return Finding("MainActivity refresh entry point", OK, "present")
    return Finding("MainActivity refresh entry point", DEGRADED, "missing: Media Library rescans will report a failure")


def _manifest(decompiled_dir: Path) -> Finding:
    manifest = decompiled_dir / "AndroidManifest.xml"
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Finding("AndroidManifest.xml", FAIL, "missing from the decompiled APK")
    if "</application>" not in text:
        return Finding("AndroidManifest.xml", FAIL, "no </application> tag to register the media receiver in")
    return Finding("AndroidManifest.xml", OK, "receiver can be registered")


def inspect_decompiled(decompiled_dir: Path) -> list[Finding]:
    """Read-only. Order is the order the report prints in."""
    return [
        _launch_hook(decompiled_dir),
        _holder(decompiled_dir),
        _asset_helper(decompiled_dir),
        _refresh_entry(decompiled_dir),
        _manifest(decompiled_dir),
    ]


def check_apk(apk_path: Path, work_dir: Path | None = None) -> CheckReport:
    """Validates, decompiles (about a minute) and inspects apk_path. work_dir
    defaults to a temp folder that's removed afterwards."""
    pi.validate_iisu_apk(apk_path)
    digest = sha256_file(apk_path)
    cleanup = work_dir is None
    work_dir = work_dir or Path(tempfile.mkdtemp(prefix="iisupc_check_"))
    try:
        decompiled = work_dir / "decompiled"
        pi.decompile(apk_path, decompiled)
        return CheckReport(
            apk_name=apk_path.name,
            sha256=digest,
            build=KNOWN_BUILDS.get(digest),
            version_name=read_version_name(decompiled),
            findings=inspect_decompiled(decompiled),
        )
    finally:
        if cleanup:
            shutil.rmtree(work_dir, ignore_errors=True)


def format_report(report: CheckReport) -> str:
    marks = {OK: "OK  ", DEGRADED: "WARN", FAIL: "FAIL"}
    if report.build:
        build_line = f"Build: {report.build} (recognized)"
    else:
        build_line = "Build: not one this project has seen before"
    lines = [
        f"File: {report.apk_name}",
        build_line,
        f"versionName: {report.version_name or 'unknown'} | SHA-256: {report.sha256}",
        "",
    ]
    lines += [f"[{marks[f.status]}] {f.name}: {f.detail}" for f in report.findings]
    lines += ["", report.verdict]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print("usage: python installer/patch_check.py <iiSU.apk>")
        return 2
    apk = Path(argv[1])
    if not apk.is_file():
        print(f"{apk} not found")
        return 2
    try:
        report = check_apk(apk)
    except RuntimeError as exc:
        print(f"Could not inspect {apk.name}: {exc}")
        return 1
    print(format_report(report))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
