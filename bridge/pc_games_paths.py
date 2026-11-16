"""Where the PC games list and its placeholders live, and the one-time move
from their old names.

PC games (programs and Steam games iiSU can launch) were called "Windows
apps" before Linux support, and their files kept that name:
bridge/windows_apps.json and bridge/windows_stubs/. They're now
bridge/pc_games.json and bridge/pc_game_stubs/. migrate_old_names() renames an
install's old files the first time anything that reads them runs (the launch
bridge, the ROM sync, the Manager), so nobody has to do anything.

Only the files on this PC change. Inside Android, iiSU still finds these games
in its own "windows" console folder, which is iiSU's name and stays.
"""

from __future__ import annotations

from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
PC_GAMES_PATH = BRIDGE_DIR / "pc_games.json"
PC_GAME_STUBS_DIR = BRIDGE_DIR / "pc_game_stubs"

OLD_PC_GAMES_NAME = "windows_apps.json"
OLD_PC_GAME_STUBS_NAME = "windows_stubs"


def migrate_old_names(bridge_dir: Path | None = None) -> list[str]:
    """Renames the old files to the new names where the new ones don't exist
    yet, and returns what was moved. When both exist (a copy restored from an
    old backup after the move, say) the new one wins and the old one is left
    alone. Never raises: a file that can't be moved is retried next time."""
    bridge_dir = bridge_dir or BRIDGE_DIR
    moved = []
    for old_name, new_name in ((OLD_PC_GAMES_NAME, PC_GAMES_PATH.name), (OLD_PC_GAME_STUBS_NAME, PC_GAME_STUBS_DIR.name)):
        old, new = bridge_dir / old_name, bridge_dir / new_name
        if old.exists() and not new.exists():
            try:
                old.rename(new)
                moved.append(f"{old_name} to {new_name}")
            except OSError:
                pass
    return moved
