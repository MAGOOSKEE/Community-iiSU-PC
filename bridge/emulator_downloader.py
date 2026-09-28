"""
Downloads and installs the PC emulators iiSU's console mappings expect,
straight from each platform's own package manager, so setting up a fresh
install doesn't mean hunting down and installing a dozen emulators by
hand first.

On Windows: winget (Windows Package Manager), already present on any
current Windows 10/11 install.
On Linux: Flatpak (user-scope install via Flathub, no root/sudo needed),
installed executables land under
~/.local/share/flatpak/exports/bin, which shared/emulator_defaults.py's
own search already covers once that folder is on PATH or listed in
config.json's search_roots.

Read-only/no-op if neither package manager is available; this is a
convenience on top of the existing "point Emulators at whatever you
already installed" flow, never a requirement.
"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

EMULATOR_CATALOG = [
    {
        "id": "retroarch",
        "name": "RetroArch",
        "systems": "NES, SNES, N64, GBA, GB/GBC, Genesis, etc.",
        "flatpak_id": "org.libretro.RetroArch",
        "winget_id": "Libretro.RetroArch",
        "check_names": ["retroarch", "org.libretro.RetroArch", "retroarch.exe"],
        "description": "Multi-system frontend covering 8-bit, 16-bit, and 32-bit consoles.",
    },
    {
        "id": "duckstation",
        "name": "DuckStation",
        "systems": "Sony PlayStation (PS1)",
        "flatpak_id": "org.duckstation.DuckStation",
        "winget_id": "DuckStation.DuckStation",
        "check_names": ["duckstation-qt", "org.duckstation.DuckStation", "duckstation-qt-x64-ReleaseLTCG.exe"],
        "description": "Fast and accurate PlayStation 1 emulator.",
    },
    {
        "id": "pcsx2",
        "name": "PCSX2",
        "systems": "Sony PlayStation 2 (PS2)",
        "flatpak_id": "net.pcsx2.PCSX2",
        "winget_id": "PCSX2.PCSX2",
        "check_names": ["pcsx2-qt", "pcsx2", "net.pcsx2.PCSX2", "pcsx2-qt.exe", "pcsx2.exe"],
        "description": "Open-source PlayStation 2 emulator.",
    },
    {
        "id": "dolphin",
        "name": "Dolphin",
        "systems": "Nintendo GameCube & Wii",
        "flatpak_id": "org.DolphinEmu.dolphin-emu",
        "winget_id": "DolphinEmulator.Dolphin",
        "check_names": ["dolphin-emu", "org.DolphinEmu.dolphin-emu", "Dolphin.exe", "DolphinQt2.exe"],
        "description": "GameCube and Wii emulator with high-definition rendering.",
    },
    {
        "id": "cemu",
        "name": "Cemu",
        "systems": "Nintendo Wii U",
        "flatpak_id": "info.cemu.Cemu",
        "winget_id": "Cemu.Cemu",
        "check_names": ["cemu", "Cemu", "info.cemu.Cemu", "Cemu.exe"],
        "description": "Highly optimized Nintendo Wii U emulator.",
    },
    {
        "id": "ppsspp",
        "name": "PPSSPP",
        "systems": "Sony PlayStation Portable (PSP)",
        "flatpak_id": "org.ppsspp.PPSSPP",
        "winget_id": "HenrikRydgard.PPSSPP",
        "check_names": ["PPSSPPSDL", "ppsspp", "org.ppsspp.PPSSPP", "PPSSPPWindows64.exe"],
        "description": "Fast PlayStation Portable emulator.",
    },
    {
        "id": "melonds",
        "name": "melonDS",
        "systems": "Nintendo DS",
        "flatpak_id": "net.kuribo64.melonDS",
        "winget_id": "melonDS.melonDS",
        "check_names": ["melonDS", "melonds", "net.kuribo64.melonDS", "melonDS.exe"],
        "description": "Fast and accurate Nintendo DS emulator.",
    },
    {
        "id": "azahar",
        "name": "Azahar",
        "systems": "Nintendo 3DS",
        "flatpak_id": "org.azahar_emu.Azahar",
        "winget_id": "",
        "check_names": ["azahar", "citra-qt", "org.azahar_emu.Azahar", "citra-qt.exe", "azahar.exe"],
        "description": "Actively maintained Nintendo 3DS emulator.",
    },
    {
        "id": "flycast",
        "name": "Flycast",
        "systems": "Sega Dreamcast",
        "flatpak_id": "org.flycast.Flycast",
        "winget_id": "Flyinghead.Flycast",
        "check_names": ["flycast", "org.flycast.Flycast", "flycast.exe"],
        "description": "Multi-platform Sega Dreamcast emulator.",
    },
    {
        "id": "rpcs3",
        "name": "RPCS3",
        "systems": "Sony PlayStation 3 (PS3)",
        "flatpak_id": "net.rpcs3.RPCS3",
        "winget_id": "RPCS3.RPCS3",
        "check_names": ["rpcs3", "net.rpcs3.RPCS3", "rpcs3.exe"],
        "description": "PlayStation 3 emulator and debugger.",
    },
    {
        "id": "xemu",
        "name": "xemu",
        "systems": "Microsoft Xbox (Original)",
        "flatpak_id": "app.xemu.xemu",
        "winget_id": "MBorgerson.xemu",
        "check_names": ["xemu", "app.xemu.xemu", "xemu.exe"],
        "description": "Original Xbox emulator.",
    },
]


def detect_backend() -> tuple[str, str]:
    """Returns (backend, path_or_message): backend is "flatpak", "winget",
    or "none" (path_or_message is then a human-readable reason why)."""
    if sys.platform != "win32":
        flatpak_path = shutil.which("flatpak")
        if flatpak_path:
            return "flatpak", flatpak_path
        return "none", "Flatpak is not installed. Install Flatpak to download emulators automatically."
    winget_path = shutil.which("winget")
    if winget_path:
        return "winget", winget_path
    return "none", "Winget (Windows Package Manager) is not available."


def is_emulator_installed(emu: dict, search_roots: list[Path] | None = None) -> bool:
    for name in emu["check_names"]:
        if shutil.which(name):
            return True

    if sys.platform != "win32":
        # A just-installed Flatpak export might not be on PATH yet in
        # this process (a shell restart normally picks it up), so also
        # check the well-known user export directory directly.
        user_flatpak_bin = Path.home() / ".local" / "share" / "flatpak" / "exports" / "bin"
        for name in emu["check_names"]:
            if (user_flatpak_bin / name).is_file():
                return True

    if search_roots:
        for root in search_roots:
            if not root.is_dir():
                continue
            for name in emu["check_names"]:
                if (root / name).is_file():
                    return True
    return False


def get_catalog_with_status(search_roots: list[Path] | None = None) -> list[dict]:
    catalog = []
    for item in EMULATOR_CATALOG:
        entry = dict(item)
        entry["installed"] = is_emulator_installed(entry, search_roots)
        catalog.append(entry)
    return catalog


def ensure_flathub_remote() -> None:
    """Adds Flathub as a user-scope remote if it isn't already configured,
    a one-time, idempotent prerequisite for `flatpak install` to find any
    of these packages at all on a fresh Flatpak setup."""
    if sys.platform == "win32":
        return
    try:
        subprocess.run(
            ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", "https://dl.flathub.org/repo/flathub.flatpakrepo"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def install_emulator(emu: dict, on_output: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """Installs one catalog entry with whichever backend detect_backend()
    finds, streaming the install command's output to on_output as it
    runs (a Flatpak/winget install can take a while for a large
    emulator). Returns (success, error_message)."""
    backend, info = detect_backend()
    if backend == "none":
        return False, info

    if backend == "flatpak":
        flatpak_id = emu.get("flatpak_id")
        if not flatpak_id:
            return False, f"No Flatpak package available for {emu['name']}."
        ensure_flathub_remote()
        cmd = ["flatpak", "install", "-y", "--user", "--noninteractive", "flathub", flatpak_id]
    else:
        winget_id = emu.get("winget_id")
        if not winget_id:
            return False, f"No Winget package available for {emu['name']}."
        cmd = ["winget", "install", "--id", winget_id, "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"]

    if on_output:
        on_output(f"> {' '.join(cmd)}\n")
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            if on_output:
                on_output(line)
        process.wait()
    except OSError as e:
        return False, str(e)
    if process.returncode == 0:
        return True, ""
    return False, f"{backend} install failed with exit code {process.returncode}"
