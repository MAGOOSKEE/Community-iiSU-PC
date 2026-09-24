"""
Quick manual test: opens the new Qt Manager window for real interaction.

This is the REAL bridge.ui.app entry point against your REAL config.json,
windows_apps.json, and ROM library -- EVERY page and sub-feature in the
Manager is now the real thing, not a test double: Home, ROM Directory,
Emulators, Windows Apps (incl. Steam import/health check), Console Games
browser, Android Storage (needs the AVD running), Media Library
(Check/Restore/preview incl. soundbite playback, plus the full iiDB
Browser -- search/preview/cart/install), Display, Advanced, Backup &
Restore, Diagnostics, Credits, and Uninstall.

Anything that WRITES real state (Save on a settings page, Windows Apps
add/edit/remove, Android Storage upload/delete/rename, Uninstall's Remove
Everything, Backup & Restore's Restore, Media Library's Restore, iiDB
Install All) does exactly what it says -- this isn't a sandboxed copy.
Searching/browsing/previewing (Diagnostics, Android Storage, Console
Games, Media Library, iiDB search) is safe to click through freely.

Run with:
    python try_manager.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bridge.ui.app import main

main()
