"""What each emulator's program is called on Linux.

config.json's "emulators" map and shared/emulator_defaults.py name every
emulator by its Windows executable (duckstation-qt-x64-ReleaseLTCG.exe,
pcsx2-qt.exe). No Linux install has a file by those names, so on Linux every
default mapping found nothing and no game could launch, however the search
folders were set (issue #14: EmuDeck on SteamOS, pointed at both the launcher
scripts and the AppImages).

Instead of a second set of mappings, the lookup translates each Windows name
into the names the same emulator goes by on Linux, so a config written by any
version, or copied over from Windows, keeps working:

  - the native binary a distro package installs (pcsx2-qt, dolphin-emu),
  - the Flatpak app ID, which is the launcher's file name under
    <flatpak>/exports/bin (net.pcsx2.PCSX2),
  - EmuDeck's launcher script under ~/Emulation/tools/launchers (pcsx2-qt.sh),
  - an AppImage, whose file name carries a version and architecture, so it is
    matched by how it starts (pcsx2-v2.4.0-linux-appimage-x64-Qt.AppImage).

A name the table doesn't know (a custom mapping) falls back to its stem with
the same endings, and a name that isn't a Windows .exe is used as written, so
a mapping can also name a Linux program directly.

Pure data and functions, so it is tested from any platform.
"""

from __future__ import annotations

from pathlib import Path

# Windows exe name (lowercase) -> (exact Linux file names, AppImage name starts).
LINUX_EQUIVALENTS: dict[str, tuple[list[str], list[str]]] = {
    "duckstation-qt-x64-releaseltcg.exe": (
        ["duckstation-qt", "duckstation", "org.duckstation.DuckStation", "duckstation.sh"],
        ["duckstation"],
    ),
    "dolphin.exe": (
        ["dolphin-emu", "org.DolphinEmu.dolphin-emu", "dolphin-emu.sh", "dolphin.sh"],
        ["dolphin"],
    ),
    "dolphinqt2.exe": (
        ["dolphin-emu", "org.DolphinEmu.dolphin-emu", "dolphin-emu.sh", "dolphin.sh"],
        ["dolphin"],
    ),
    "cemu.exe": (["cemu", "info.cemu.Cemu", "cemu.sh", "cemu-native.sh"], ["cemu"]),
    "citra-qt.exe": (
        ["azahar", "citra-qt", "org.azahar_emu.Azahar", "azahar.sh", "citra.sh"],
        ["azahar", "citra"],
    ),
    "azahar.exe": (["azahar", "org.azahar_emu.Azahar", "azahar.sh"], ["azahar"]),
    "ppssppwindows64.exe": (
        ["PPSSPPSDL", "PPSSPPQt", "ppsspp", "org.ppsspp.PPSSPP", "ppsspp.sh"],
        ["ppsspp"],
    ),
    "pcsx2-qt.exe": (["pcsx2-qt", "net.pcsx2.PCSX2", "pcsx2-qt.sh", "pcsx2.sh"], ["pcsx2"]),
    "pcsx2.exe": (["pcsx2-qt", "pcsx2", "net.pcsx2.PCSX2", "pcsx2-qt.sh", "pcsx2.sh"], ["pcsx2"]),
    "melonds.exe": (["melonDS", "net.kuribo64.melonDS", "melonds.sh"], ["melonds"]),
    "flycast.exe": (["flycast", "org.flycast.Flycast", "flycast.sh"], ["flycast"]),
    "rpcs3.exe": (["rpcs3", "net.rpcs3.RPCS3", "rpcs3.sh"], ["rpcs3"]),
    "vita3k.exe": (["Vita3K", "vita3k.sh"], ["vita3k"]),
    "citron.exe": (["citron", "citron.sh"], ["citron"]),
    "ryujinx.exe": (["Ryujinx", "io.github.ryubing.Ryujinx", "ryujinx.sh"], ["ryujinx"]),
    "xemu.exe": (["xemu", "app.xemu.xemu", "xemu-emu.sh", "xemu.sh"], ["xemu"]),
    "xenia_canary.exe": (["xenia_canary", "xenia.sh"], ["xenia"]),
    "xenia.exe": (["xenia", "xenia.sh"], ["xenia"]),
    "retroarch.exe": (["retroarch", "org.libretro.RetroArch", "retroarch.sh"], ["retroarch"]),
}

APPIMAGE_SUFFIX = ".appimage"


def linux_names_for(exe_name: str) -> tuple[list[str], list[str]]:
    """(exact file names, AppImage name starts) to look for on Linux for one
    configured executable name. A name that isn't a Windows .exe is kept as
    written and nothing else is guessed for it."""
    known = LINUX_EQUIVALENTS.get(exe_name.casefold())
    if known is not None:
        return list(known[0]), list(known[1])
    if not exe_name.casefold().endswith(".exe"):
        return [exe_name], []
    stem = exe_name[:-4]
    return [stem, stem.lower(), f"{stem.lower()}.sh"], [stem.lower()]


def linux_matcher(exe_names: list[str]):
    """A (file name) -> bool test covering every Linux name of every
    configured executable. Names compare without regard to case, since the
    same emulator ships as melonDS, melonds.sh and MelonDS.AppImage."""
    exact: set[str] = set()
    appimage_starts: list[str] = []
    for exe_name in exe_names:
        names, starts = linux_names_for(exe_name)
        exact.update(name.casefold() for name in names)
        appimage_starts.extend(start.casefold() for start in starts)

    def matches(file_name: str) -> bool:
        folded = file_name.casefold()
        if folded in exact:
            return True
        return folded.endswith(APPIMAGE_SUFFIX) and any(folded.startswith(start) for start in appimage_starts)

    return matches


def linux_path_names(exe_names: list[str]) -> list[str]:
    """The exact names to try on PATH, in order, without duplicates."""
    ordered: list[str] = []
    for exe_name in exe_names:
        for name in linux_names_for(exe_name)[0]:
            if name not in ordered:
                ordered.append(name)
    return ordered


def default_linux_search_roots(home: Path | None = None) -> list[Path]:
    """Where emulators usually live on Linux, searched after the configured
    folders so nobody has to know these paths: Flatpak's launchers (per user
    and system wide), EmuDeck's launcher scripts and its AppImage folder, and
    the usual per-user program folders."""
    home = home or Path.home()
    return [
        home / ".local" / "share" / "flatpak" / "exports" / "bin",
        Path("/var/lib/flatpak/exports/bin"),
        home / "Emulation" / "tools" / "launchers",
        home / "Applications",
        home / ".local" / "bin",
    ]


def linux_core_argument(argument: str) -> str:
    """A RetroArch core argument written for Windows ("cores/snes9x_libretro.dll")
    as Linux RetroArch takes it ("snes9x_libretro.so"). Given a bare core file
    name, RetroArch looks it up in its own configured cores folder, which is
    somewhere else for a distro package, a Flatpak and an AppImage. Anything
    else is returned unchanged."""
    normalized = argument.replace("\\", "/")
    if normalized.startswith("cores/") and normalized.casefold().endswith("_libretro.dll"):
        return normalized.rsplit("/", 1)[-1][:-4] + ".so"
    return argument
