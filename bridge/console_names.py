"""
Resolves a ROM folder name to one of iiSU's own known console short names
(see console_names.json, extracted from iiSU's own bundled
emuladores_default.json). Shared by sync_library.py (to build the AVD's
placeholder mirror) and the Manager (to show whether iiSU will actually
recognize your ROM folders before you save).
"""

import difflib
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
    # The longest overlap wins: "Playstation2" holds "playstation" (PS1) but
    # sits inside "sonyplaystation2", and the first hit used to win.
    best, best_overlap = None, 0
    for key, short in by_compact.items():
        if len(key) < 5:
            continue
        overlap = len(key) if key in c else len(c) if c in key else 0
        if overlap > best_overlap:
            best, best_overlap = short, overlap
    return best


# Words people add to a console folder's name that say nothing about which
# console it is ("SNES Roms", "N64 games").
FILLER_WORDS = {"rom", "roms", "game", "games", "iso", "isos", "collection", "library"}


def suggest_folder_name(folder_name: str, exact: dict, by_compact: dict) -> str | None:
    """The name to rename an unrecognized console folder to, or None when
    nothing is close enough to guess. Drops filler words first, then allows
    a small typo ("dreamcst"). The suggestion is iiSU's own short name for
    the console, which always resolves."""
    words = [w for w in re.split(r"[\s_\-\.]+", folder_name) if w and w.lower() not in FILLER_WORDS]
    trimmed = " ".join(words)
    if trimmed and trimmed != folder_name:
        short = resolve_console_shortname(trimmed, exact, by_compact)
        if short:
            return short
    close = difflib.get_close_matches(compact(trimmed or folder_name), list(by_compact), n=1, cutoff=0.75)
    return by_compact[close[0]] if close else None

