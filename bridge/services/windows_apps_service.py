"""Non-UI logic behind the Windows Apps page, extracted out of
bridge/manager.py's tkinter Manager class ahead of porting that page to Qt
(the two largest pages -- this one and Media Library -- get their service
layer extracted first, per the Qt rewrite plan, so the eventual page file
is just "wire a service call to a signal/slot").

Every function here takes its state as plain arguments instead of reading
instance attributes, and reports errors by raising WindowsAppsServiceError
instead of popping up a messagebox -- whichever UI toolkit calls this
decides how (or whether) to show that to the user.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
WINDOWS_APPS_PATH = BRIDGE_DIR / "windows_apps.json"
WINDOWS_STUBS_DIR = BRIDGE_DIR / "windows_stubs"


class WindowsAppsServiceError(Exception):
    """Raised instead of showing a messagebox directly -- callers decide
    how to present this (a QMessageBox, a log line, etc.)."""


# -- windows_apps.json -------------------------------------------------


def load_windows_apps() -> dict:
    if not WINDOWS_APPS_PATH.is_file():
        return {}
    try:
        data = json.loads(WINDOWS_APPS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise WindowsAppsServiceError(f"Couldn't read {WINDOWS_APPS_PATH.name}:\n\n{e}") from e
    return data if isinstance(data, dict) else {}


def save_windows_apps(apps: dict) -> None:
    try:
        WINDOWS_APPS_PATH.write_text(json.dumps(apps, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        raise WindowsAppsServiceError(f"Couldn't save {WINDOWS_APPS_PATH.name}:\n\n{e}") from e


def ensure_added_at(entry: dict) -> dict:
    entry = dict(entry)
    entry.setdefault("added_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    return entry


# -- Placeholder (.pcgame) filenames -------------------------------------------------


def windows_reserved_filename(name: str) -> bool:
    """True for Windows-reserved DOS device filenames (CON, COM1, ...),
    which are invalid even with an extension (CON.txt, COM1.pcgame)."""
    stem = name.rstrip(" .").split(".", 1)[0].upper()
    return (
        stem in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"COM[1-9]", stem) is not None
        or re.fullmatch(r"LPT[1-9]", stem) is not None
    )


def safe_pcgame_name(name: str) -> str | None:
    name = name.strip()
    if not name or name in {".", ".."}:
        return None
    if any(ch in name for ch in '<>:"/\\|?*'):
        return None
    if windows_reserved_filename(name):
        return None
    return name


def safe_steam_pcgame_name(name: str) -> str:
    """Make a Steam title safe as a Windows/.pcgame filename."""
    cleaned = re.sub(r'[<>:"/\\|?*]+', " - ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return "Steam Game"
    if windows_reserved_filename(cleaned):
        cleaned += " - Game"
    return cleaned


def unique_windows_app_name(base: str, apps: dict) -> str:
    """Return a collision-free app/placeholder name."""
    if base not in apps:
        return base
    number = 2
    while f"{base} ({number})" in apps:
        number += 1
    return f"{base} ({number})"


# -- Legacy stub-folder migration -------------------------------------------------


def legacy_windows_stubs_dir(roms_dir: str) -> Path | None:
    if not roms_dir:
        return None
    legacy_dir = Path(roms_dir) / "windows"
    return legacy_dir if legacy_dir.is_dir() else None


def migrate_legacy_windows_stubs(roms_dir: str) -> int:
    """One-time, additive copy of pre-relocation .pcgame placeholders from
    <roms_dir>/windows into WINDOWS_STUBS_DIR. Never deletes or modifies
    anything under roms_dir -- that's live content in the user's own ROM
    directory. Returns how many files were copied (0 if there was nothing
    to migrate)."""
    legacy_dir = legacy_windows_stubs_dir(roms_dir)
    if legacy_dir is None:
        return 0
    try:
        WINDOWS_STUBS_DIR.mkdir(parents=True, exist_ok=True)
        copied = 0
        for stub in legacy_dir.glob("*.pcgame"):
            target = WINDOWS_STUBS_DIR / stub.name
            if not target.exists():
                shutil.copy2(stub, target)
                copied += 1
        return copied
    except OSError:
        return 0


def delete_legacy_windows_stubs(roms_dir: str) -> None:
    legacy_dir = legacy_windows_stubs_dir(roms_dir)
    if legacy_dir is None:
        raise WindowsAppsServiceError("No old placeholder folder found under your ROM directory -- nothing to delete.")
    try:
        shutil.rmtree(legacy_dir)
    except OSError as e:
        raise WindowsAppsServiceError(f"Couldn't delete {legacy_dir}:\n\n{e}") from e


# -- Steam discovery -------------------------------------------------


def valid_uri(uri: str) -> bool:
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://\S+$", uri.strip()))


def steam_app_id(value: str) -> str | None:
    """Accept an App ID, Steam protocol URI, or Steam store URL."""
    value = value.strip()
    if value.isdigit():
        return value

    patterns = (
        r"^steam://(?:run|rungameid)/(\d+)(?:[/?#].*)?$",
        r"^https?://(?:store\.)?steampowered\.com/app/(\d+)(?:[/?#].*)?$",
        r"^https?://steamcommunity\.com/app/(\d+)(?:[/?#].*)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, value, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def is_steam_uri(uri: str) -> bool:
    return bool(re.match(r"^steam://(?:run|rungameid)/\d+(?:[/?#].*)?$", uri.strip(), re.IGNORECASE))


def parse_steam_vdf_strings(text: str) -> dict[str, str]:
    """Small VDF reader for the flat key/value data we need from Steam files."""
    return {m.group(1): m.group(2).replace(r"\\", "\\") for m in re.finditer(r'"([^"]+)"\s*"([^"]*)"', text)}


def steam_library_paths() -> list[Path]:
    """Find Steam plus every configured library folder without requiring Steam APIs."""
    candidates: list[Path] = []
    env_candidates = [
        os.environ.get("PROGRAMFILES(X86)", ""),
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for base in env_candidates:
        if base:
            candidates.extend([Path(base) / "Steam", Path(base) / "steam"])
    try:
        import winreg

        for root, key_name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(root, key_name) as key:
                    for value_name in ("SteamPath", "InstallPath"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                            if value:
                                candidates.append(Path(str(value)))
                        except OSError:
                            pass
            except OSError:
                pass
    except Exception:
        pass

    steam_root = next((p for p in candidates if (p / "steamapps").is_dir()), None)
    if steam_root is None:
        return []

    libraries = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            raw = vdf.read_text(encoding="utf-8", errors="ignore")
            for match in re.finditer(r'"path"\s*"([^"]+)"', raw):
                path = Path(match.group(1).replace(r"\\", "\\"))
                if (path / "steamapps").is_dir():
                    libraries.append(path)
        except OSError:
            pass

    unique: list[Path] = []
    seen: set[str] = set()
    for path in libraries:
        key = str(path.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def installed_steam_games() -> list[dict]:
    games: list[dict] = []
    seen: set[str] = set()
    for library in steam_library_paths():
        steamapps = library / "steamapps"
        for manifest in steamapps.glob("appmanifest_*.acf"):
            try:
                fields = parse_steam_vdf_strings(manifest.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            appid = fields.get("appid") or manifest.stem.removeprefix("appmanifest_")
            name = fields.get("name")
            installdir = fields.get("installdir", "")
            if not appid.isdigit() or not name or appid in seen:
                continue
            seen.add(appid)
            games.append(
                {
                    "appid": appid,
                    "name": name,
                    "library": str(library),
                    "install_dir": str(steamapps / "common" / installdir) if installdir else "",
                }
            )
    return sorted(games, key=lambda g: g["name"].casefold())


def installed_steam_ids() -> set[str]:
    """App IDs currently represented by local Steam manifests."""
    return {game["appid"] for game in installed_steam_games()}


def steam_ids_already_added(apps: dict) -> set[str]:
    ids: set[str] = set()
    for entry in apps.values():
        if isinstance(entry, dict) and str(entry.get("type", "executable")).lower() == "uri":
            appid = steam_app_id(str(entry.get("uri", "")))
            if appid:
                ids.add(appid)
    return ids


def steam_artwork_cache_file(appid: str) -> Path:
    return BRIDGE_DIR / "cache" / "steam_artwork" / f"{appid}.jpg"


# -- Per-row status/display -------------------------------------------------


def windows_app_status(name: str, entry: dict, installed_steam_ids_set: set[str] | None = None) -> str:
    if not isinstance(entry, dict):
        return "✗ Invalid entry"

    launch_type = str(entry.get("type", "executable")).lower()
    if launch_type == "uri":
        uri = entry.get("uri", "")
        if not isinstance(uri, str) or not valid_uri(uri):
            return "✗ Invalid URI"
        if is_steam_uri(uri):
            appid = steam_app_id(uri)
            if installed_steam_ids_set is not None and appid and appid not in installed_steam_ids_set:
                return "○ Steam game not installed"
    elif launch_type == "executable":
        exe = entry.get("exe", "")
        if not isinstance(exe, str) or not exe.strip():
            return "✗ No EXE"
        if not Path(os.path.expandvars(os.path.expanduser(exe))).is_file():
            return "✗ Missing EXE"
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            return "✗ Invalid args"
        working = entry.get("working_dir")
        if working and not Path(os.path.expandvars(os.path.expanduser(str(working)))).is_dir():
            return "✗ Missing work dir"
    else:
        return "✗ Unknown type"

    if not (WINDOWS_STUBS_DIR / f"{name}.pcgame").is_file():
        return "✗ Missing placeholder"
    return "✓ Ready"


def windows_app_display_type(entry: dict) -> str:
    launch_type = str(entry.get("type", "executable")).lower()
    if launch_type == "uri":
        return "Steam Game" if is_steam_uri(str(entry.get("uri", ""))) else "Custom URI"
    return "Executable"


# -- Create / edit / remove / repair placeholders -------------------------------------------------


def create_windows_app(name: str, entry: dict, apps: dict, windows_dir: Path) -> None:
    """Adds `name` to `apps` (in place) and creates its .pcgame placeholder.
    Raises on a case-insensitive name collision or filesystem failure --
    callers that already validated uniqueness (e.g. edit, which excludes
    the entry being edited) should check before calling this."""
    if any(existing.casefold() == name.casefold() for existing in apps):
        raise WindowsAppsServiceError(f"A Windows app named '{name}' already exists.")
    entry = ensure_added_at(entry)
    try:
        windows_dir.mkdir(parents=True, exist_ok=True)
        (windows_dir / f"{name}.pcgame").touch(exist_ok=True)
    except OSError as e:
        raise WindowsAppsServiceError(f"Couldn't create the .pcgame placeholder:\n\n{e}") from e
    apps[name] = entry


def rename_windows_app_placeholder(windows_dir: Path, old_name: str, new_name: str) -> None:
    old_stub = windows_dir / f"{old_name}.pcgame"
    new_stub = windows_dir / f"{new_name}.pcgame"
    try:
        windows_dir.mkdir(parents=True, exist_ok=True)
        if old_stub != new_stub and old_stub.exists():
            old_stub.rename(new_stub)
        else:
            new_stub.touch(exist_ok=True)
    except OSError as e:
        raise WindowsAppsServiceError(f"Couldn't update the .pcgame placeholder:\n\n{e}") from e


def remove_windows_app_placeholder(windows_dir: Path, name: str) -> None:
    try:
        (windows_dir / f"{name}.pcgame").unlink(missing_ok=True)
    except OSError:
        pass


def repair_missing_placeholders(windows_dir: Path, names: list[str]) -> int:
    """Recreates any of `names` whose placeholder is missing. Returns how
    many were actually (re)created."""
    windows_dir.mkdir(parents=True, exist_ok=True)
    repaired = 0
    for name in names:
        placeholder = windows_dir / f"{name}.pcgame"
        if not placeholder.exists():
            placeholder.touch()
            repaired += 1
    return repaired


def find_missing_and_orphan_placeholders(apps: dict, windows_dir: Path) -> tuple[list[str], list[Path]]:
    """Cross-references configured app names against placeholder files on
    disk: missing = a mapping with no .pcgame file, orphans = a .pcgame
    file with no mapping."""
    windows_dir.mkdir(parents=True, exist_ok=True)
    mapped = {name.casefold(): name for name in apps}
    placeholders = {p.stem.casefold(): p for p in windows_dir.glob("*.pcgame") if p.is_file()}
    missing = [name for key, name in mapped.items() if key not in placeholders]
    orphans = [path for key, path in placeholders.items() if key not in mapped]
    return missing, orphans


# -- Health scan -------------------------------------------------


def scan_windows_apps_health(apps: dict, windows_dir: Path, installed_steam_ids_set: set[str]) -> dict:
    """Non-destructive health findings for Windows Apps and placeholders."""
    findings = {
        "missing_placeholders": [],
        "orphan_placeholders": [],
        "missing_executables": [],
        "uninstalled_steam": [],
        "invalid_uris": [],
        "invalid_entries": [],
        "duplicate_steam_ids": [],
    }

    steam_owners: dict[str, list[str]] = {}

    for name, entry in apps.items():
        if not isinstance(entry, dict):
            findings["invalid_entries"].append(name)
            continue

        if not (windows_dir / f"{name}.pcgame").is_file():
            findings["missing_placeholders"].append(name)

        launch_type = str(entry.get("type", "executable")).lower()
        if launch_type == "executable":
            exe = entry.get("exe", "")
            if not isinstance(exe, str) or not exe.strip():
                findings["missing_executables"].append(name)
            else:
                expanded = Path(os.path.expandvars(os.path.expanduser(exe)))
                if not expanded.is_file():
                    findings["missing_executables"].append(name)
        elif launch_type == "uri":
            uri = entry.get("uri", "")
            if not isinstance(uri, str) or not valid_uri(uri):
                findings["invalid_uris"].append(name)
                continue
            appid = steam_app_id(uri)
            if appid:
                steam_owners.setdefault(appid, []).append(name)
                if appid not in installed_steam_ids_set:
                    findings["uninstalled_steam"].append(name)
        else:
            findings["invalid_entries"].append(name)

    for appid, names in steam_owners.items():
        if len(names) > 1:
            findings["duplicate_steam_ids"].append((appid, names))

    if windows_dir.is_dir():
        mapped = {name.casefold() for name in apps}
        try:
            for placeholder in windows_dir.glob("*.pcgame"):
                if placeholder.stem.casefold() not in mapped:
                    findings["orphan_placeholders"].append(placeholder.stem)
        except OSError:
            pass

    return findings


# -- Export / import -------------------------------------------------


def build_export_payload(apps: dict) -> dict:
    return {"format": "iisu-pc-windows-apps", "version": 1, "apps": apps}


def parse_import_payload(payload) -> dict | None:
    """Accepts either the wrapped export format ({"apps": {...}}) or a bare
    apps mapping. A non-dict top level has nothing to extract from and is
    treated as an empty mapping, not an error -- matches the original
    behavior this was ported from."""
    incoming = payload.get("apps", payload) if isinstance(payload, dict) else {}
    return incoming if isinstance(incoming, dict) else None


def import_windows_apps(incoming: dict, apps: dict, windows_dir: Path, existing_steam_ids: set[str]) -> tuple[int, int]:
    """Merges `incoming` into `apps` (in place), skipping invalid names,
    existing names, and Steam games already mapped by App ID. Returns
    (added, skipped)."""
    windows_dir.mkdir(parents=True, exist_ok=True)
    added = skipped = 0
    for raw_name, raw_entry in incoming.items():
        name = safe_pcgame_name(str(raw_name))
        if not name or not isinstance(raw_entry, dict) or name in apps:
            skipped += 1
            continue
        incoming_steam_id = steam_app_id(str(raw_entry.get("uri", "")))
        if incoming_steam_id and incoming_steam_id in existing_steam_ids:
            skipped += 1
            continue
        entry = ensure_added_at(raw_entry)
        apps[name] = entry
        try:
            (windows_dir / f"{name}.pcgame").touch(exist_ok=True)
        except OSError:
            apps.pop(name, None)
            skipped += 1
            continue
        added += 1
        if incoming_steam_id:
            existing_steam_ids.add(incoming_steam_id)
    return added, skipped


# -- Launching -------------------------------------------------


def resolve_uri_launch(entry: dict) -> str:
    uri = entry.get("uri")
    if not isinstance(uri, str) or not valid_uri(uri):
        raise WindowsAppsServiceError("This entry has an invalid URI.")
    return uri.strip()


def resolve_executable_launch(entry: dict) -> tuple[Path, list[str], Path]:
    """Returns (executable, args, working_dir), all validated to exist.
    Raises WindowsAppsServiceError with a user-facing message otherwise."""
    exe_value = entry.get("exe")
    if not isinstance(exe_value, str) or not exe_value.strip():
        raise WindowsAppsServiceError("This entry has no executable configured.")

    executable = Path(os.path.expandvars(os.path.expanduser(exe_value)))
    if not executable.is_file():
        raise WindowsAppsServiceError(f"The configured executable does not exist:\n\n{executable}")

    args = entry.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise WindowsAppsServiceError("This entry has invalid arguments. The args value must be a list of strings.")

    working_dir_value = entry.get("working_dir")
    working_dir = (
        Path(os.path.expandvars(os.path.expanduser(str(working_dir_value)))) if working_dir_value else executable.parent
    )
    if not working_dir.is_dir():
        raise WindowsAppsServiceError(f"The configured working directory does not exist:\n\n{working_dir}")

    return executable, args, working_dir


def windows_app_sort_key(column: str, name: str, entry: dict, display_type: str, status: str):
    """column is one of "name"/"type"/"status"/"added" -- callers already
    have display_type/status computed per row for the UI, so this takes
    them rather than recomputing (which would also need the installed-
    Steam-ids set threaded through again for no benefit)."""
    if column == "type":
        return display_type.casefold()
    if column == "status":
        return status.casefold()
    if column == "added":
        return str(entry.get("added_at", "")) if isinstance(entry, dict) else ""
    return name.casefold()
