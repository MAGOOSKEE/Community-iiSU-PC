"""
Default console -> PC emulator mappings, curated from iiSU's own bundled
emuladores_default.json (its list of consoles and, per console, which
Android packages it recognizes as compatible emulators).

Two shapes, matching bridge/config.json's "emulators" map:

STANDALONE_DEFAULTS: one Android package -> one PC executable. The
Android package chosen is iiSU's own top-priority candidate for that
console (the first "emulators" entry with a "packages" list), it's
stubbed (see installer/stub_apk.py) purely so iiSU's installed-package
check resolves that console to a package our patched LaunchBridge already
recognizes. What that Android package's real app would actually do is
irrelevant: our patch intercepts the launch before Android would ever
start it, and redirects to whatever PC executable is configured here
instead, regardless of whether the two projects are related at all (e.g.
PS2 is stubbed under AetherSX2's package but redirected to PCSX2 on PC,
there's no Android PCSX2 to begin with).

RETROARCH_BY_EXTENSION: a *fallback* for consoles routed through a single
shared com.retroarch stub, used only when iiSU doesn't report which core
it actually launched with (see bridge/launch_bridge.py's
find_emulator_for_package, the LIBRETRO intent extra is trusted first
and unconditionally whenever it's present, since it identifies the
console directly instead of guessing from the ROM's file extension, which
can be ambiguous, .chd is chdman's container for both PS1 and Dreamcast
-- or just plain missing for a console never explicitly added to this
list at all, e.g. an arcade/MAME core resolved from a .zip). Only
extensions that don't collide with another entry in this same map are
included here, for the same reason Saturn and MAME/arcade are left out
entirely: without a reported core to fall back on, their extensions
(.bin/.cue/.iso/.chd/.zip/.7z) overlap too broadly with everything else
on this list to guess correctly from the extension alone.

RetroArch itself is launched as retroarch.exe -L <core> -f <rom>, so
pre_args includes -L and a core path relative to wherever retroarch.exe
is found (standard "cores/xxx_libretro.dll" layout), this assumes the
matching core is already installed there (launch_bridge.py's
ensure_retroarch_core downloads a missing one from the libretro buildbot
automatically), same as RetroArch itself needs to be already installed
for this to do anything.
"""

from collections import Counter

STANDALONE_DEFAULTS = [
    {
        "console": "psx",
        "console_label": "Sony PlayStation",
        "package": "com.github.stenzek.duckstation",
        "app_label": "DuckStation",
        "exe_names": ["duckstation-qt-x64-ReleaseLTCG.exe"],
        "pre_args": ["-fullscreen"],
    },
    {
        "console": "gc",
        "console_label": "Nintendo GameCube",
        "package": "org.dolphinemu.dolphinemu",
        "app_label": "Dolphin",
        "exe_names": ["Dolphin.exe", "DolphinQt2.exe"],
        # Dolphin has no dedicated --fullscreen flag, confirmed live: -b alone
        # leaves it in a normal titled window. -C lets a CLI launch override
        # a config value for just this run without touching the user's saved
        # Dolphin.ini, Display.Fullscreen=True is the one that actually
        # covers the whole screen (verified via screenshot, no OS window
        # border at all).
        "pre_args": ["-b", "-C", "Dolphin.Display.Fullscreen=True"],
    },
    {
        "console": "wii",
        "console_label": "Nintendo Wii",
        "package": "org.dolphinemu.dolphinemu",
        "app_label": "Dolphin",
        "exe_names": ["Dolphin.exe", "DolphinQt2.exe"],
        "pre_args": ["-b", "-C", "Dolphin.Display.Fullscreen=True"],
    },
    {
        "console": "wiiu",
        "console_label": "Nintendo Wii U",
        "package": "info.cemu.cemu",
        "app_label": "Cemu",
        "exe_names": ["Cemu.exe"],
        # Confirmed live: bare "-f" errors out with a parameter-parse dialog
        # ("the argument '<rom path>' for option '--fullscreen' is invalid"),
        # this Cemu build's -f/--fullscreen always consumes the next
        # token as its required on/off value, so a bare trailing rom path
        # (how every other emulator here takes its rom, see
        # launch_bridge.py's `args = [exe, *pre_args, rom_path]`) gets
        # swallowed as that value instead. Needs an explicit value, and the
        # rom path needs its own --game flag rather than relying on
        # position.
        "pre_args": ["-f", "true", "--game"],
    },
    {
        "console": "3ds",
        "console_label": "Nintendo 3DS",
        "package": "org.citra.citra_emu",
        "app_label": "Citra",
        "exe_names": ["citra-qt.exe"],
        "pre_args": ["-f"],
    },
    {
        "console": "3ds",
        "console_label": "Nintendo 3DS (Azahar)",
        "package": "org.azahar_emu.azahar",
        "app_label": "Azahar",
        # Azahar Plus (a further fork of Azahar) renamed its binary to
        # azahar.exe, mainline Azahar builds still ship as citra-qt.exe,
        # inherited from Azahar's own Citra ancestry. Both are searched for
        # under this one slot since they're the same PC-side choice from
        # iiSU's perspective, just two forks' different binary names.
        "exe_names": ["citra-qt.exe", "azahar.exe"],
        "pre_args": ["-f"],
    },
    {
        "console": "psp",
        "console_label": "Sony PlayStation Portable",
        "package": "org.ppsspp.ppsspp",
        "app_label": "PPSSPP",
        "exe_names": ["PPSSPPWindows64.exe"],
        "pre_args": ["--fullscreen"],
    },
    {
        "console": "ps2",
        "console_label": "Sony PlayStation 2",
        "package": "xyz.aethersx2.android",
        "app_label": "PCSX2",
        "exe_names": ["pcsx2-qt.exe", "pcsx2.exe"],
        "pre_args": ["-fullscreen"],
    },
    {
        "console": "nds",
        "console_label": "Nintendo DS",
        "package": "me.magnum.melonds",
        "app_label": "melonDS",
        "exe_names": ["melonDS.exe"],
        # Confirmed live via -f/--fullscreen (verified with a screenshot,
        # covers the whole screen).
        "pre_args": ["-f"],
    },
    {
        "console": "nds",
        "console_label": "Nintendo DS (melonDualDS)",
        # melonDualDS is a separate Android package, not a suffixed variant
        # of me.magnum.melonds, "me.magnum.melondualds".startswith("me.magnum.melonds")
        # is False (they diverge after "melond"), so without this as its
        # own entry a melonDualDS install would never match the prefix
        # lookup in launch_bridge.find_emulator_for_package at all.
        "package": "me.magnum.melondualds",
        "app_label": "melonDS",
        "exe_names": ["melonDS.exe"],
        "pre_args": ["-f"],
    },
    {
        "console": "dreamcast",
        "console_label": "Sega Dreamcast",
        "package": "com.flycast.emulator",
        "app_label": "Flycast",
        "exe_names": ["flycast.exe"],
        # Flycast has no dedicated fullscreen CLI flag at all (its --help
        # only lists -config section:key=value and -help), it's a transient
        # override of the same emu.cfg key its own [window] fullscreen
        # setting uses. Confirmed live via screenshot, no OS window border.
        "pre_args": ["-config", "window:fullscreen=yes"],
    },
    {
        "console": "ps3",
        "console_label": "Sony PlayStation 3",
        "package": "aenu.aps3e",
        "app_label": "RPCS3",
        "exe_names": ["rpcs3.exe"],
        "pre_args": ["--no-gui", "--fullscreen"],
        # RPCS3's actual CLI order is the opposite of every other
        # emulator here: "rpcs3.exe <game_path> --no-gui --fullscreen",
        # confirmed live, the boot target has to come *before* these
        # flags or RPCS3 parses neither flag as having a boot target at
        # all ("Missing command-line arguments! Cannot run no-gui mode
        # without boot target."). See launch_bridge.py's rom_before_args
        # handling.
        "rom_before_args": True,
    },
    {
        "console": "psvita",
        "console_label": "Sony PlayStation Vita",
        "package": "org.vita3k.emulator",
        "app_label": "Vita3K",
        "exe_names": ["Vita3K.exe"],
        "pre_args": ["-F"],
    },
    {
        "console": "switch",
        "console_label": "Nintendo Switch",
        "package": "org.citron.citron_emu",
        "app_label": "Citron",
        # Citron is the actively-maintained continuation of Yuzu after
        # Yuzu's takedown; both packages below are routed to the same
        # citron.exe since that's the only Switch emulator this project
        # can point to now. No Ryujinx entry exists here on purpose,
        # iiSU's own bundled emulator list has no Ryujinx package at all,
        # so iiSU would never report it as the launching package regardless
        # of whether it's installed on the PC side.
        "exe_names": ["citron.exe"],
        "pre_args": ["-f"],
    },
    {
        "console": "switch",
        "console_label": "Nintendo Switch (Citron EA)",
        "package": "org.citron.citron_emu.ea",
        "app_label": "Citron",
        "exe_names": ["citron.exe"],
        "pre_args": ["-f"],
    },
    {
        "console": "switch",
        "console_label": "Nintendo Switch (Yuzu)",
        "package": "org.yuzu.yuzu_emu",
        "app_label": "Citron",
        "exe_names": ["citron.exe"],
        "pre_args": ["-f"],
    },
    {
        "console": "xbox",
        "console_label": "Microsoft Xbox",
        "package": "com.izzy2lost.x1box",
        "app_label": "xemu",
        "exe_names": ["xemu.exe"],
        # xemu doesn't accept a bare trailing ROM path, unlike every other
        # emulator here, it collides with the CD drive xemu.toml already
        # persists from whatever was last loaded, producing "drive with
        # bus=0, unit=0 (index=0) exists" and an immediate exit(1). Confirmed
        # live: -dvd_path is the flag that actually sets the disc.
        # -full-screen (QEMU's own generic display flag, not xemu-specific)
        # confirmed live too: without it xemu opens a normal titled window.
        "pre_args": ["-full-screen", "-dvd_path"],
    },
    {
        "console": "xbox360",
        "console_label": "Microsoft Xbox 360",
        "package": "emu.x360.mobile",
        "app_label": "Xenia",
        # Xenia Canary is the actively-maintained fork, mainline xenia.exe
        # is kept as a fallback for whichever's actually installed, same
        # pattern as the Citra/Azahar and melonDS/melonDualDS slots above.
        "exe_names": ["xenia_canary.exe", "xenia.exe"],
        "pre_args": ["--fullscreen"],
    },
    {
        "console": "steam",
        "console_label": "Valve Steam (via GameNative)",
        "package": "app.gamenative",
        "app_label": "Steam",
        # exe_names/pre_args are never actually used for this one,
        # launch_bridge.py special-cases app.gamenative entirely (it
        # launches via Steam's own steam://rungameid/<id> URI handler, not
        # a subprocess.Popen'd exe, since the "ROM" here is really just a
        # Steam App ID). This entry exists only so a stub gets built and
        # installed for it (see installer/stub_apk.py) and so it shows up
        # in the Emulators settings table at all.
        "exe_names": ["steam.exe"],
        "pre_args": [],
    },
]

RETROARCH_PACKAGE = "com.retroarch"
RETROARCH_APP_LABEL = "RetroArch"

# iiSU lists RetroArch as its *first*-priority candidate for most consoles
# (including ones with a dedicated STANDALONE_DEFAULTS entry above, like
# PS1 and Dreamcast), if you ever explicitly pick "RetroArch" instead of
# the dedicated standalone option for one of those in iiSU's own settings,
# this is what keeps that launch redirecting somewhere real instead of
# trying to run a RetroArch that was only ever a stub.
RETROARCH_SAFETY_NET_EXTENSIONS = {
    ".cue": ("psx", ["duckstation-qt-x64-ReleaseLTCG.exe"], ["-fullscreen"]),
    ".bin": ("psx", ["duckstation-qt-x64-ReleaseLTCG.exe"], ["-fullscreen"]),
    ".pbp": ("psx", ["duckstation-qt-x64-ReleaseLTCG.exe"], ["-fullscreen"]),
    ".chd": ("psx", ["duckstation-qt-x64-ReleaseLTCG.exe"], ["-fullscreen"]),
    ".m3u": ("psx", ["duckstation-qt-x64-ReleaseLTCG.exe"], ["-fullscreen"]),
    ".cdi": ("dreamcast", ["flycast.exe"], []),
    ".gdi": ("dreamcast", ["flycast.exe"], []),
}

RETROARCH_BY_EXTENSION = [
    {
        "console": "nes",
        "console_label": "Nintendo Entertainment System",
        "core": "fceumm_libretro.dll",
        "extensions": [".nes", ".fds", ".unf", ".unif"],
    },
    {
        "console": "snes",
        "console_label": "Super Nintendo Entertainment System",
        "core": "snes9x_libretro.dll",
        "extensions": [".sfc", ".smc", ".bs", ".bsx", ".swc"],
    },
    {
        "console": "n64",
        "console_label": "Nintendo 64",
        "core": "mupen64plus_next_libretro.dll",
        "extensions": [".n64", ".z64", ".v64"],
    },
    {
        "console": "gba",
        "console_label": "Nintendo Game Boy Advance",
        "core": "mgba_libretro.dll",
        "extensions": [".gba"],
    },
    {
        "console": "gb",
        "console_label": "Nintendo Game Boy / Color",
        "core": "gambatte_libretro.dll",
        "extensions": [".gb", ".gbc"],
    },
    {
        "console": "genesis",
        "console_label": "Sega Genesis / Mega Drive",
        "core": "genesis_plus_gx_libretro.dll",
        "extensions": [".md", ".gen", ".smd", ".sms", ".gg", ".32x"],
    },
]


# A handful of Android libretro cores append a GPU-backend suffix that
# Windows builds don't use (Windows resolves the rendering backend a
# different way, not via the core filename), these need an explicit
# remap rather than the generic "strip _android, swap .so for .dll"
# transform retroarch_core_dll_for_android_core() otherwise applies.
ANDROID_CORE_NAME_OVERRIDES = {
    "mupen64plus_next_gles3": "mupen64plus_next",
    "mupen64plus_next_gles2": "mupen64plus_next",
}

# The reverse of RETROARCH_SAFETY_NET_EXTENSIONS' extension -> console
# guess, but keyed by the *actual* core RetroArch/iiSU reports launching
# (via retroarch_core_dll_for_android_core) instead of the ROM's file
# extension. Needed because some formats are genuinely ambiguous by
# extension alone, .chd is chdman's container for both PS1 CDs and
# Dreamcast GD-ROMs/CDs, but RETROARCH_SAFETY_NET_EXTENSIONS can only
# point ".chd" at one of them (DuckStation). A Dreamcast game shipped as
# .chd would silently launch DuckStation instead of Flycast without this
# override, confirmed as the cause of "some Dreamcast games won't
# launch": the LIBRETRO extra (flycast_libretro_android.so) is present
# and unambiguous even when the extension isn't, so it's checked first
# and wins outright, the same way an extension safety-net entry already
# wins outright over the generic by-extension RetroArch guess.
RETROARCH_CORE_OVERRIDES = {
    "flycast_libretro.dll": ("dreamcast", ["flycast.exe"], []),
}


def retroarch_core_dll_for_android_core(android_core_filename: str) -> str | None:
    """Translates the Android libretro core .so filename iiSU/RetroArch
    itself reports launching (the LIBRETRO intent extra) into the matching
    Windows core .dll filename, so a RetroArch launch uses whichever core
    is actually configured on the Android side instead of this module's
    own per-extension guess (RETROARCH_BY_EXTENSION), the two can
    legitimately disagree, since the curated guess is only a reasonable
    per-console default, not necessarily what a given install actually
    has configured. Returns None if the filename doesn't look like a
    libretro-android core at all, so the caller can fall back to the
    extension-based guess."""
    if not android_core_filename.endswith("_libretro_android.so"):
        return None
    core_name = android_core_filename.removesuffix("_libretro_android.so")
    core_name = ANDROID_CORE_NAME_OVERRIDES.get(core_name, core_name)
    return f"{core_name}_libretro.dll"


def standalone_profile_for_core_dll(core_dll: str) -> dict | None:
    """The dedicated-emulator profile for a resolved Windows core dll, per
    RETROARCH_CORE_OVERRIDES, in the same {exe_names, pre_args} shape as
    any other emulators-map entry, or None if this core has no dedicated
    override (the ordinary by-extension guess applies instead)."""
    override = RETROARCH_CORE_OVERRIDES.get(core_dll)
    if override is None:
        return None
    _console, exe_names, pre_args = override
    return {"exe_names": exe_names, "pre_args": pre_args}


def build_emulators_map() -> dict:
    """Builds the full bridge/config.json "emulators" map from the two
    lists above. Standalone entries with the same package (e.g. GameCube
    and Wii both under Dolphin) collapse into one entry automatically."""
    emulators = {}
    for entry in STANDALONE_DEFAULTS:
        profile = {
            "exe_names": entry["exe_names"],
            "pre_args": entry["pre_args"],
        }
        if entry.get("rom_before_args"):
            profile["rom_before_args"] = True
        emulators[entry["package"]] = profile

    by_extension = {}
    for ext, (_console, exe_names, pre_args) in RETROARCH_SAFETY_NET_EXTENSIONS.items():
        by_extension[ext] = {"exe_names": exe_names, "pre_args": pre_args}
    for entry in RETROARCH_BY_EXTENSION:
        for ext in entry["extensions"]:
            by_extension[ext] = {
                "exe_names": ["retroarch.exe"],
                "pre_args": ["-L", f"cores/{entry['core']}", "-f"],
            }
    emulators[RETROARCH_PACKAGE] = {"by_extension": by_extension}

    return emulators


def all_stub_packages() -> list[tuple[str, str]]:
    """Every (package, app_label) that needs a redirector stub installed
    for these defaults to actually resolve in iiSU, one per unique
    package, RetroArch included once even though it covers many consoles."""
    seen = {}
    for entry in STANDALONE_DEFAULTS:
        seen.setdefault(entry["package"], entry["app_label"])
    seen.setdefault(RETROARCH_PACKAGE, RETROARCH_APP_LABEL)
    return list(seen.items())


def all_emulator_exe_names() -> list[tuple[str, list[str]]]:
    """(app_label, exe_names) for every standalone emulator plus RetroArch
    itself, one per unique label, for UIs that want to check which of
    these are actually installed under a set of search folders (see
    bridge/launch_bridge.py's find_executable, which this is meant to be
    used with for a result that matches what a real launch would find)."""
    seen = {}
    for entry in STANDALONE_DEFAULTS:
        seen.setdefault(entry["app_label"], entry["exe_names"])
    seen.setdefault(RETROARCH_APP_LABEL, ["retroarch.exe"])
    return list(seen.items())


def describe_profile(profile: dict) -> tuple[str, str]:
    """Human-readable (executable name(s), launch flags) for one
    config.json "emulators" map entry, for UIs that list these in a table.

    A "by_extension" entry (RetroArch) has neither a flat exe_names nor
    pre_args list, it maps a different one of each per ROM extension,
    so reading those keys directly gives an empty string on both columns,
    which reads as "nothing configured" even though it's fully set up.
    Showing every distinct exe_names value across all mapped extensions
    would surface RETROARCH_SAFETY_NET_EXTENSIONS' DuckStation/Flycast
    redirects too, which reads as a jumbled, unrelated list, the single
    most common executable across all mapped extensions is what a user
    actually means by "what does this run," so that's what's shown; only
    the flags genuinely vary per extension."""
    if "by_extension" in profile:
        by_ext = profile["by_extension"]
        exe_counts = Counter(name for entry in by_ext.values() for name in entry.get("exe_names", []))
        primary_exe = exe_counts.most_common(1)[0][0] if exe_counts else "?"
        return primary_exe, f"(varies by file extension, {len(by_ext)} mapped)"
    return ", ".join(profile.get("exe_names", [])), ", ".join(profile.get("pre_args", []))
