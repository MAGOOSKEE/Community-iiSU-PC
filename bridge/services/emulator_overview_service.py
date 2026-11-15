"""The console-first view of the emulator mappings: one row per console
("Sony PlayStation 2: PCSX2, found") instead of the raw table of Android
package names, executable names and launch flags that config.json stores.

Shared by the Manager's PC Emulators page and the onboarding wizard, kept
free of Qt so the row building and the "which emulators does my library
need" logic can be tested without a GUI.

The rows are built from whatever "emulators" map is passed in (the page's
current, possibly unsaved, mappings), so a change made in the raw table or
through Change Emulator shows up here straight away.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from emulator_downloader import EMULATOR_CATALOG
from emulator_lookup import find_executable
from shared.emulator_defaults import (
    RETROARCH_APP_LABEL,
    RETROARCH_BY_EXTENSION,
    RETROARCH_PACKAGE,
    STANDALONE_DEFAULTS,
)

# iiSU's own short names that mean the same console as one of
# emulator_defaults' console keys (a "3ds" ROM folder resolves to iiSU's
# "n3ds", and so on).
CONSOLE_ALIASES = {"n3ds": "3ds", "gbc": "gb", "sgb": "gb", "megadrive": "genesis"}

# Short console names for summaries ("You have PS2 and GameCube games").
SHORT_CONSOLE_NAMES = {
    "psx": "PlayStation", "ps2": "PS2", "ps3": "PS3", "psp": "PSP", "psvita": "PS Vita",
    "gc": "GameCube", "wii": "Wii", "wiiu": "Wii U", "3ds": "3DS", "nds": "DS",
    "switch": "Switch", "dreamcast": "Dreamcast", "xbox": "Xbox", "xbox360": "Xbox 360",
    "steam": "Steam", "nes": "NES", "snes": "SNES", "n64": "N64", "gba": "Game Boy Advance",
    "gb": "Game Boy", "genesis": "Genesis",
}

STATUS_FOUND = "found"
STATUS_MISSING = "missing"
STATUS_BY_LINK = "by_link"


@dataclass
class ConsoleRow:
    console: str
    console_label: str
    package: str
    emulator: str
    exe_names: list[str]
    pre_args: list[str]
    by_extension: bool = False
    launched_by_link: bool = False
    is_default: bool = True
    catalog_id: str | None = None
    status: str = STATUS_MISSING
    executable: Path | None = None
    in_library: bool = False
    library_files: int = 0
    shares_package_with: list[str] = field(default_factory=list)

    @property
    def can_change(self) -> bool:
        return not self.by_extension and not self.launched_by_link

    @property
    def status_text(self) -> str:
        if self.status == STATUS_BY_LINK:
            return "Opens through Steam"
        if self.status == STATUS_FOUND:
            return "Ready"
        if self.catalog_id:
            return "Not installed"
        return "Not installed (install it yourself)"


def _base_label(label: str) -> str:
    """"Nintendo 3DS (Azahar)" -> "Nintendo 3DS"."""
    return label.split(" (")[0].strip()


def catalog_id_for(exe_names: list[str]) -> str | None:
    """The Download Emulators catalog entry that installs one of these
    executables, or None for an emulator the downloader doesn't offer."""
    names = {name.lower() for name in exe_names}
    for entry in EMULATOR_CATALOG:
        if names & {name.lower() for name in entry["check_names"]}:
            return entry["id"]
    return None


def catalog_name(catalog_id: str) -> str:
    return next((entry["name"] for entry in EMULATOR_CATALOG if entry["id"] == catalog_id), catalog_id)


def emulator_choices() -> list[dict]:
    """Every known emulator a console can be switched to, one per program:
    {"label", "exe_names", "pre_args"}. RetroArch is left out because it
    needs a core chosen per file type, and Steam because it isn't one."""
    seen: dict[str, dict] = {}
    for entry in STANDALONE_DEFAULTS:
        if entry.get("launched_by_link"):
            continue
        seen.setdefault(entry["app_label"], {
            "label": entry["app_label"],
            "exe_names": list(entry["exe_names"]),
            "pre_args": list(entry["pre_args"]),
            "rom_before_args": bool(entry.get("rom_before_args")),
        })
    return sorted(seen.values(), key=lambda choice: choice["label"].lower())


def _emulator_label(profile: dict, default_entry: dict | None) -> tuple[str, bool]:
    """(name to show, whether it's still the default)."""
    exe_names = profile.get("exe_names", [])
    if default_entry is not None and exe_names == default_entry["exe_names"]:
        return default_entry["app_label"], profile.get("pre_args", []) == default_entry["pre_args"]
    for choice in emulator_choices():
        if exe_names == choice["exe_names"]:
            return choice["label"], False
    if exe_names:
        return Path(exe_names[0]).stem, False
    return "(none)", False


def build_rows(emulators: dict) -> list[ConsoleRow]:
    """One row per console the mappings can launch, in the defaults' order,
    then any custom package mapping that matches no known console."""
    rows: list[ConsoleRow] = []
    seen_consoles: set[str] = set()
    used_packages: set[str] = set()
    package_consoles: dict[str, list[str]] = {}

    for entry in STANDALONE_DEFAULTS:
        package = entry["package"]
        if package not in emulators:
            continue
        package_consoles.setdefault(package, []).append(_base_label(entry["console_label"]))

    for entry in STANDALONE_DEFAULTS:
        package = entry["package"]
        used_packages.add(package)
        if entry["console"] in seen_consoles or package not in emulators:
            continue
        seen_consoles.add(entry["console"])
        profile = emulators[package]
        label, is_default = _emulator_label(profile, entry)
        others = [c for c in package_consoles.get(package, []) if c != _base_label(entry["console_label"])]
        rows.append(ConsoleRow(
            console=entry["console"],
            console_label=_base_label(entry["console_label"]),
            package=package,
            emulator=label,
            exe_names=list(profile.get("exe_names", [])),
            pre_args=list(profile.get("pre_args", [])),
            launched_by_link=bool(entry.get("launched_by_link")),
            is_default=is_default,
            catalog_id=catalog_id_for(profile.get("exe_names", [])),
            status=STATUS_BY_LINK if entry.get("launched_by_link") else STATUS_MISSING,
            shares_package_with=others,
        ))

    used_packages.add(RETROARCH_PACKAGE)
    if RETROARCH_PACKAGE in emulators:
        for entry in RETROARCH_BY_EXTENSION:
            if entry["console"] in seen_consoles:
                continue
            seen_consoles.add(entry["console"])
            rows.append(ConsoleRow(
                console=entry["console"],
                console_label=entry.get("console_label", entry["console"]),
                package=RETROARCH_PACKAGE,
                emulator=RETROARCH_APP_LABEL,
                exe_names=["retroarch.exe"],
                pre_args=[],
                by_extension=True,
                catalog_id=catalog_id_for(["retroarch.exe"]),
            ))

    for package, profile in emulators.items():
        if package in used_packages or "exe_names" not in profile:
            continue
        label, _ = _emulator_label(profile, None)
        rows.append(ConsoleRow(
            console="",
            console_label=f"Other ({package})",
            package=package,
            emulator=label,
            exe_names=list(profile.get("exe_names", [])),
            pre_args=list(profile.get("pre_args", [])),
            is_default=False,
            catalog_id=catalog_id_for(profile.get("exe_names", [])),
        ))
    return rows


def check_installed(
    rows: list[ConsoleRow], search_roots: list[str], cache: dict | None = None,
    finder: Callable[..., Path | None] = find_executable,
) -> list[ConsoleRow]:
    """Fills in each row's status and executable, the same search a real
    launch does (so "Ready" here means the game would find its emulator).
    Slow on a first scan of a big folder, so call it off the GUI thread."""
    roots = [Path(root) for root in search_roots if root]
    cache = cache if cache is not None else {"executables": {}}
    results: dict[tuple[str, ...], Path | None] = {}
    for row in rows:
        if row.launched_by_link:
            row.status = STATUS_BY_LINK
            continue
        key = tuple(row.exe_names)
        if key not in results:
            results[key] = finder(row.exe_names, roots, cache) if row.exe_names else None
        row.executable = results[key]
        row.status = STATUS_FOUND if row.executable else STATUS_MISSING
    return rows


def library_consoles(roms_dir: str | None, resolve: Callable[[str], str | None]) -> dict[str, int]:
    """{console key: number of files} for each console folder in the ROM
    folder that holds at least one file, using emulator_defaults' console
    keys (aliases applied). resolve maps a folder name to iiSU's short name."""
    counts: dict[str, int] = {}
    if not roms_dir:
        return counts
    root = Path(roms_dir)
    if not root.is_dir():
        return counts
    for child in root.iterdir():
        if not child.is_dir():
            continue
        short = resolve(child.name)
        if not short:
            continue
        try:
            files = sum(1 for item in child.iterdir() if item.is_file())
        except OSError:
            files = 0
        if not files:
            continue
        key = CONSOLE_ALIASES.get(short, short)
        counts[key] = counts.get(key, 0) + files
    return counts


def mark_library(rows: list[ConsoleRow], consoles: dict[str, int]) -> list[ConsoleRow]:
    for row in rows:
        row.in_library = row.console in consoles
        row.library_files = consoles.get(row.console, 0)
    return rows



def sort_for_display(rows: list[ConsoleRow]) -> list[ConsoleRow]:
    """Consoles in your library first, then the rest, each in the defaults'
    order (Python's sort is stable)."""
    return sorted(rows, key=lambda row: not row.in_library)


def needed_downloads(rows: list[ConsoleRow], only_library: bool = True) -> list[str]:
    """Catalog ids of the missing emulators worth downloading: for the
    consoles in your library, or for every console."""
    ids: list[str] = []
    for row in rows:
        if row.status != STATUS_MISSING or not row.catalog_id:
            continue
        if only_library and not row.in_library:
            continue
        if row.catalog_id not in ids:
            ids.append(row.catalog_id)
    return ids


def summary(rows: list[ConsoleRow]) -> str:
    """One line for above the list, about your library when there is one."""
    library = [row for row in rows if row.in_library]
    if library:
        missing = [row for row in library if row.status == STATUS_MISSING]
        names = [SHORT_CONSOLE_NAMES.get(row.console, row.console_label) for row in library]
        if not missing:
            return f"Your library has games for {_join(names)}, and every emulator they need is ready."
        needs = sorted({row.emulator for row in missing}, key=str.lower)
        return (
            f"Your library has games for {_join(names)}. "
            f"Still needed: {_join(needs)}."
        )
    ready = sum(1 for row in rows if row.status in (STATUS_FOUND, STATUS_BY_LINK))
    return f"{ready} of {len(rows)} consoles have their emulator installed."


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def apply_choice(
    emulators: dict, package: str, exe_names: list[str], pre_args: list[str], rom_before_args: bool = False
) -> dict:
    """A copy of the mappings with one package switched to another program.
    The profile is rebuilt rather than edited, so a setting that belonged
    to the old emulator (rom_before_args) doesn't carry over to the new one."""
    updated = copy.deepcopy(emulators)
    profile: dict = {"exe_names": list(exe_names), "pre_args": list(pre_args)}
    if rom_before_args:
        profile["rom_before_args"] = True
    updated[package] = profile
    return updated
