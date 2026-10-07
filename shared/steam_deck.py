"""Steam Deck and gamescope (Steam's Game Mode compositor) detection.

Kept in shared/ with no dependencies so Setup, the Android-runtime
selection, and the Steam shortcut service can all use it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# A sane starting point for the Deck's 1280x800 panel: density follows the
# project's usual rule of scaling 240dpi@1080p by height (800/1080 * 240 is
# about 178).
STEAM_DECK_DISPLAY = {"width": 1280, "height": 800, "density": 180, "refresh_rate": 60}


def is_gamescope_session(env: Mapping[str, str] | None = None) -> bool:
    """True inside Steam's Game Mode (gamescope is a Wayland compositor)."""
    env = os.environ if env is None else env
    desktop = f"{env.get('XDG_CURRENT_DESKTOP', '')} {env.get('XDG_SESSION_DESKTOP', '')}".lower()
    return "gamescope" in desktop or bool(env.get("GAMESCOPE_WAYLAND_DISPLAY"))


def is_steam_deck(board_vendor: str | None = None, product_name: str | None = None, env: Mapping[str, str] | None = None) -> bool:
    """A Steam Deck (LCD "Jupiter", OLED "Galileo"), from the DMI strings,
    with the SteamDeck=1 environment variable SteamOS sets as a fallback.
    The DMI values are read from /sys when not given."""
    env = os.environ if env is None else env
    if env.get("SteamDeck") == "1":
        return True
    if board_vendor is None or product_name is None:
        try:
            base = Path("/sys/devices/virtual/dmi/id")
            board_vendor = (base / "board_vendor").read_text().strip()
            product_name = (base / "board_name").read_text().strip()
        except OSError:
            return False
    return board_vendor.lower() == "valve" and product_name.lower() in ("jupiter", "galileo")
