"""Adds Community-iiSU-PC to Steam as a non-Steam game, so it can be
launched from Steam's library, Big Picture, or a Steam Deck's Game Mode.

Steam keeps non-Steam shortcuts in a binary VDF file per user:
<Steam>/userdata/<account id>/config/shortcuts.vdf. This module reads and
writes that format, creates/updates/removes one shortcut in it, and finds
the file(s) on Windows and Linux (native and Flatpak Steam).

Two things worth knowing:

- Steam rewrites shortcuts.vdf from memory when it exits, so an edit made
  while Steam is running is silently lost (or overwritten). Callers must
  check steam_is_running() and ask the user to close Steam first.
- Every write keeps a timestamped copy of the previous file next to it.

The shortcut runs bridge/start_iisu_pc.py (the same thing the desktop
shortcut does), so it starts the Android VM and the launch bridge without
opening the Manager window. That script exits once everything is up, so
Steam will show the "game" as stopped almost immediately; the VM and bridge
keep running on their own.

The Steam Deck / gamescope detection helpers live in shared/steam_deck.py
and are re-exported here.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import zlib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from shared.platform_compat import IS_WINDOWS, subprocess_creationflags
from shared.steam_deck import STEAM_DECK_DISPLAY, is_gamescope_session, is_steam_deck  # noqa: F401; re-exported for callers

# Binary VDF type tags.
_MAP, _STRING, _INT32, _END = 0x00, 0x01, 0x02, 0x08

SHORTCUT_NAME = "Community-iiSU-PC"
START_SCRIPT = Path(__file__).resolve().parent.parent / "start_iisu_pc.py"


class SteamShortcutError(Exception):
    pass


# == Binary VDF ==

def parse_vdf(data: bytes) -> dict:
    """Parses Steam's binary VDF into nested dicts (maps), str, and int
    (unsigned 32-bit). Raises SteamShortcutError on anything it doesn't
    understand rather than guessing, so a file this can't round-trip is
    never overwritten."""
    pos = 0

    def read_cstring() -> str:
        nonlocal pos
        end = data.find(b"\x00", pos)
        if end < 0:
            raise SteamShortcutError("shortcuts.vdf is truncated")
        text = data[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        return text

    def read_map() -> dict:
        nonlocal pos
        result: dict = {}
        while True:
            if pos >= len(data):
                raise SteamShortcutError("shortcuts.vdf is truncated")
            tag = data[pos]
            pos += 1
            if tag == _END:
                return result
            key = read_cstring()
            if tag == _MAP:
                result[key] = read_map()
            elif tag == _STRING:
                result[key] = read_cstring()
            elif tag == _INT32:
                if pos + 4 > len(data):
                    raise SteamShortcutError("shortcuts.vdf is truncated")
                (result[key],) = struct.unpack_from("<I", data, pos)
                pos += 4
            else:
                raise SteamShortcutError(f"shortcuts.vdf has a field type (0x{tag:02x}) this tool doesn't handle, leaving it alone")

    result = read_map()
    return result


def serialize_vdf(tree: Mapping) -> bytes:
    out = bytearray()

    def write_map(mapping: Mapping) -> None:
        for key, value in mapping.items():
            encoded = str(key).encode("utf-8") + b"\x00"
            if isinstance(value, Mapping):
                out.append(_MAP)
                out.extend(encoded)
                write_map(value)
            elif isinstance(value, bool):
                out.append(_INT32)
                out.extend(encoded + struct.pack("<I", int(value)))
            elif isinstance(value, int):
                out.append(_INT32)
                out.extend(encoded + struct.pack("<I", value & 0xFFFFFFFF))
            else:
                out.append(_STRING)
                out.extend(encoded + str(value).encode("utf-8") + b"\x00")
        out.append(_END)

    write_map(tree)
    return bytes(out)


# == Shortcut entries ==

def shortcut_app_id(exe: str, app_name: str) -> int:
    """The app id Steam derives for a non-Steam shortcut (CRC32 of the Exe
    string as written plus the name, high bit set)."""
    return (zlib.crc32((exe + app_name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def build_entry(name: str, exe: str, start_dir: str, launch_options: str = "", icon: str = "") -> dict:
    """One shortcut's fields in the order Steam writes them. exe is stored
    quoted, as Steam does."""
    quoted_exe = exe if exe.startswith('"') else f'"{exe}"'
    return {
        "appid": shortcut_app_id(quoted_exe, name),
        "AppName": name,
        "Exe": quoted_exe,
        "StartDir": start_dir if start_dir.startswith('"') else f'"{start_dir}"',
        "icon": icon,
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "sortas": "",
        "tags": {},
    }


def _entries(tree: dict) -> dict:
    shortcuts = tree.setdefault("shortcuts", {})
    if not isinstance(shortcuts, dict):
        raise SteamShortcutError("shortcuts.vdf has an unexpected layout")
    return shortcuts


def find_entry_key(tree: dict, name: str) -> str | None:
    for key, entry in _entries(tree).items():
        if isinstance(entry, dict) and entry.get("AppName") == name:
            return key
    return None


def upsert_entry(tree: dict, entry: dict) -> str:
    """Adds the entry, or replaces the one with the same AppName in place
    (keeping its position and play time). Returns "added" or "updated"."""
    shortcuts = _entries(tree)
    existing = find_entry_key(tree, entry["AppName"])
    if existing is not None:
        entry = {**entry, "LastPlayTime": shortcuts[existing].get("LastPlayTime", 0), "tags": shortcuts[existing].get("tags", {})}
        shortcuts[existing] = entry
        return "updated"
    numeric = [int(k) for k in shortcuts if str(k).isdigit()]
    shortcuts[str(max(numeric) + 1 if numeric else 0)] = entry
    return "added"


def remove_entry(tree: dict, name: str) -> bool:
    shortcuts = _entries(tree)
    key = find_entry_key(tree, name)
    if key is None:
        return False
    del shortcuts[key]
    # Steam expects consecutive "0", "1", ... keys.
    renumbered = {str(i): entry for i, entry in enumerate(shortcuts.values())}
    shortcuts.clear()
    shortcuts.update(renumbered)
    return True


# == Files on disk ==

def read_shortcuts_file(path: Path) -> dict:
    if not path.is_file():
        return {"shortcuts": {}}
    return parse_vdf(path.read_bytes())


def write_shortcuts_file(path: Path, tree: dict) -> Path | None:
    """Writes atomically after saving a timestamped copy of the existing
    file; returns the backup path (None if there was nothing to back up)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.is_file():
        backup = path.with_name(f"shortcuts.vdf.iisupc-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(path, backup)
    temp = path.with_name(path.name + ".iisupc-tmp")
    temp.write_bytes(serialize_vdf(tree))
    temp.replace(path)
    return backup


def steam_roots() -> list[Path]:
    """Steam install folders that contain a userdata directory."""
    from services import windows_apps_service

    roots = []
    for candidate in windows_apps_service.steam_library_paths()[:1]:
        if (candidate / "userdata").is_dir():
            roots.append(candidate)
    return roots


def shortcuts_files(roots: list[Path] | None = None) -> list[Path]:
    """Every <Steam>/userdata/<account id>/config/shortcuts.vdf location
    (existing or not) for real accounts (numeric, non-zero ids)."""
    files = []
    for root in roots if roots is not None else steam_roots():
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for account in sorted(userdata.iterdir()):
            if account.is_dir() and account.name.isdigit() and account.name != "0":
                files.append(account / "config" / "shortcuts.vdf")
    return files


def steam_is_running() -> bool:
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
                capture_output=True, text=True, timeout=10, creationflags=subprocess_creationflags(),
            )
            return "steam.exe" in result.stdout.lower()
        return subprocess.run(["pgrep", "-x", "steam"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def launcher_entry(python_exe: str | None = None, start_script: Path = START_SCRIPT) -> dict:
    """The shortcut that starts Community-iiSU-PC the same way the desktop
    shortcut does."""
    exe = python_exe or sys.executable
    return build_entry(
        SHORTCUT_NAME,
        exe=exe,
        start_dir=str(start_script.parent),
        launch_options=f'"{start_script}"',
    )


def add_to_steam(files: list[Path] | None = None, entry: dict | None = None) -> list[tuple[Path, str]]:
    """Adds (or updates) the shortcut for every Steam account found.
    Returns [(file, "added"|"updated")]. Raises SteamShortcutError if Steam
    is running (it would overwrite the change) or no account is found."""
    if steam_is_running():
        raise SteamShortcutError("Steam is running. Close Steam completely first (it rewrites this file when it exits and would undo the change), then try again.")
    targets = files if files is not None else shortcuts_files()
    if not targets:
        raise SteamShortcutError("Couldn't find a Steam user account. Start Steam and sign in once, then close it and try again.")
    entry = entry or launcher_entry()
    results = []
    for path in targets:
        tree = read_shortcuts_file(path)
        outcome = upsert_entry(tree, entry)
        write_shortcuts_file(path, tree)
        results.append((path, outcome))
    return results


def remove_from_steam(files: list[Path] | None = None) -> int:
    """Removes the shortcut everywhere; returns how many files changed."""
    if steam_is_running():
        raise SteamShortcutError("Steam is running. Close Steam completely first, then try again.")
    changed = 0
    for path in files if files is not None else shortcuts_files():
        if not path.is_file():
            continue
        tree = read_shortcuts_file(path)
        if remove_entry(tree, SHORTCUT_NAME):
            write_shortcuts_file(path, tree)
            changed += 1
    return changed
