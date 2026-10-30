"""Which iiSU APK this install last patched in, so the Updates page can tell
whether the latest official release is newer.

iiSU's own version name (what Android reports, 0.1.6.1 for Alpha 7.4) and its
GitHub release tags (0.0.7.4 for that same build) use different numbering, so
the two can't be compared. The release's APK file name (iiSU-Alpha-7.4.apk)
can, as long as it's recorded when Setup or an iiSU update installs it. The
version name is kept too: if Android later reports a different one, iiSU was
replaced some other way and the record no longer says what's installed.
"""

import json
from pathlib import Path

RECORD_PATH = Path(__file__).resolve().parent.parent / "bridge" / ".iisu_installed.json"

# The version of the patch this project applies to iiSU (installer/smali_patch/
# and how installer/patch_iisu.py rewrites iiSU). Bump it, and add a line to
# PATCH_CHANGES, whenever an iiSU patched with the previous version needs
# repatching: the Manager then won't start iiSU until it is (see
# bridge/ui/dialogs/repatch_wizard.py). tests/test_iisu_install_record.py fails
# when smali_patch/ changes and this hasn't.
PATCH_VERSION = 2
# A record from before patch versions were kept, or no record at all.
UNVERSIONED_PATCH = 1
PATCH_CHANGES = {
    2: "Android apps such as Chrome and the Play Store open from iiSU, also after a headless start.",
}


def save(apk_name: str, version_name: str | None, path: Path = RECORD_PATH, patch_version: int = PATCH_VERSION) -> None:
    """Best effort: without a record the Updates page just can't compare."""
    try:
        path.write_text(
            json.dumps({"apk_name": apk_name, "version_name": version_name, "patch_version": patch_version}),
            encoding="utf-8",
        )
    except OSError:
        pass


def patch_version_of(record: dict | None) -> int:
    """The patch version iiSU was installed with. An install from before patch
    versions were kept, or with no record at all, counts as the oldest."""
    value = (record or {}).get("patch_version", UNVERSIONED_PATCH)
    return value if isinstance(value, int) else UNVERSIONED_PATCH


def needs_repatch(record: dict | None) -> bool:
    return patch_version_of(record) < PATCH_VERSION


def changes_since(record: dict | None) -> list[str]:
    """What repatching brings, oldest change first."""
    installed = patch_version_of(record)
    return [PATCH_CHANGES[version] for version in sorted(PATCH_CHANGES) if version > installed]


def load(path: Path = RECORD_PATH) -> dict | None:
    """The record, or None when there is none (installed before records were
    kept) or it can't be read."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("apk_name"), str) and data["apk_name"] else None
