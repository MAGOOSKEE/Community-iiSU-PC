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


def save(apk_name: str, version_name: str | None, path: Path = RECORD_PATH) -> None:
    """Best effort: without a record the Updates page just can't compare."""
    try:
        path.write_text(json.dumps({"apk_name": apk_name, "version_name": version_name}), encoding="utf-8")
    except OSError:
        pass


def load(path: Path = RECORD_PATH) -> dict | None:
    """The record, or None when there is none (installed before records were
    kept) or it can't be read."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("apk_name"), str) and data["apk_name"] else None
