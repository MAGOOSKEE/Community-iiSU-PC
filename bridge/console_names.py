"""
Resolves a ROM folder name to one of iiSU's own known console short names
(see console_names.json, extracted from iiSU's own bundled
emuladores_default.json). Shared by sync_library.py (to build the AVD's
placeholder mirror) and manager.py (to show whether iiSU will actually
recognize your ROM folders before you save).
"""

import json
import re
from pathlib import Path

CONSOLE_NAMES_PATH = Path(__file__).parent / "console_names.json"

# Manual overrides for folder names that don't line up 1:1 with iiSU's
# shortName/longName/alternativeNames (e.g. iiSU expects "Sony PlayStation
# 2", a real folder might just be named "Playstation 2"). Extend this if
# you add a console folder that doesn't auto-match.
NAME_OVERRIDES = {
    "playstation 1": "psx",
    "playstation 2": "ps2",
    "playstation 3": "ps3",
    "playstation portable": "psp",
    "sega megadrive (genesis)": "genesis",
}


def compact(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def load_console_lookup() -> tuple[dict, dict]:
    with open(CONSOLE_NAMES_PATH, encoding="utf-8") as f:
        exact = json.load(f)
    by_compact = {compact(k): v for k, v in exact.items()}
    return exact, by_compact


def resolve_console_shortname(folder_name: str, exact: dict, by_compact: dict) -> str | None:
    lower = folder_name.lower()
    if lower in NAME_OVERRIDES:
        return NAME_OVERRIDES[lower]
    if lower in exact:
        return exact[lower]
    c = compact(folder_name)
    if c in by_compact:
        return by_compact[c]
    # Fallback: substring match against longer (>=5 char) known names only,
    # to avoid short codes like "gc" false-positive matching inside names.
    for key, short in by_compact.items():
        if len(key) >= 5 and (key in c or c in key):
            return short
    return None
