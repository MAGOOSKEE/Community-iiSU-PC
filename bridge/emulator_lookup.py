"""Finding the PC-side emulator and ROM for a launch request, and building the
command line it is started with. Pure lookups with no process, window or
socket state, split out of launch_bridge.py so they can be tested and imported
without its Windows-only modules.

Search results are cached in .path_cache.json next to this file (see
load_path_cache).
"""

import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

# A raw script launch only puts its own folder on sys.path, and shared/ sits
# next to bridge/, not inside it.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.emulator_defaults import (  # noqa: E402; path set up above
    all_emulator_exe_names,
    retroarch_core_dll_for_android_core,
    standalone_profile_for_core_dll,
)

PATH_CACHE_PATH = Path(__file__).parent / ".path_cache.json"
LIBRETRO_CORE_URL = "https://buildbot.libretro.com/nightly/windows/x86_64/latest/{core_dll}.zip"


def load_path_cache() -> dict:
    if not PATH_CACHE_PATH.is_file():
        return {"executables": {}, "roms": {}}
    try:
        cache = json.loads(PATH_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"executables": {}, "roms": {}}
    cache.setdefault("executables", {})
    cache.setdefault("roms", {})
    return cache


def save_path_cache(cache: dict) -> None:
    try:
        PATH_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass



EXECUTABLE_SEARCH_MAX_DEPTH = 4
ROM_SEARCH_MAX_DEPTH = 3


_FIND_BY_NAME_SKIP_DIRS = {".git", ".cache", ".var", ".wine", ".steam", "node_modules", "proc", "sys", "dev"}


def _find_by_name(root: Path, names: set[str], max_depth: int) -> Path | None:
    """Breadth-first search for any file in `names` under root, capped at
    max_depth directory levels below root (root's direct children are
    depth 0). Replaces Path.rglob(), whose unbounded recursion means one
    slow, unavoidable round-trip per folder for every folder in the
    *entire* tree when root lives on a network share (search_roots and
    roms_dir are both commonly a Windows-mapped X:\\ drive here),
    exactly the "takes forever" scan this exists to fix. Emulator installs
    and this project's curated ROM folders are never more than a few
    levels deep, so a modest cap avoids wandering into irrelevant,
    deeply-nested subfolders an rglob can't tell apart from a real match
    ahead of time, an emulator's own save states/BIOS/thumbnails/cache
    dirs, or (worse, since it recurses *into* it for no reason) a
    multi-gigabyte Xbox 360 title's own .data folder sitting right next to
    the file actually being searched for. BFS also means a shallower
    match is always returned over a deeper coincidental same-name file,
    which rglob's traversal order doesn't guarantee at all."""
    current = [root]
    depth = 0
    while current and depth <= max_depth:
        next_level = []
        for directory in current:
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                if entry.name in names and entry.is_file():
                    return Path(entry.path)
            for entry in entries:
                # follow_symlinks=False: a symlink loop (real on Linux,
                # e.g. some Flatpak/Steam library layouts) would otherwise
                # recurse forever; skip a handful of directory names that
                # are either huge, irrelevant, or themselves loop-prone.
                if entry.is_dir(follow_symlinks=False) and entry.name not in _FIND_BY_NAME_SKIP_DIRS:
                    next_level.append(Path(entry.path))
        current = next_level
        depth += 1
    return None


def find_executable(names: list[str], search_roots: list[Path], cache: dict) -> Path | None:
    """Scanning search_roots (which commonly include all of C:/Program
    Files) on every single launch is real, avoidable latency, the result
    almost never changes between launches, so it's cached by exe name and
    only re-scanned if the cached path stops existing (e.g. the emulator
    got moved/reinstalled elsewhere).

    The cache key includes search_roots itself (not just the exe names),
    so editing search_roots in the Manager naturally invalidates the old
    entry instead of it staying wrong until the previously-found file
    happens to disappear, the Manager documents that path changes apply
    on the very next launch with no restart needed, and a stale cache hit
    would quietly break that."""
    cache_key = "|".join(names) + "::" + "|".join(str(r) for r in search_roots)
    cached = cache["executables"].get(cache_key)
    if cached and Path(cached).is_file():
        return Path(cached)

    # A Flatpak-installed emulator's exported binary is already on PATH,
    # checking that first is both a real shortcut and the only way
    # find_executable() would ever notice it at all (search_roots are
    # plain folders, not APT/Flatpak; shutil.which() handles both).
    for name in names:
        which_path = shutil.which(name)
        if which_path and Path(which_path).is_file():
            cache["executables"][cache_key] = which_path
            return Path(which_path)

    for root in search_roots:
        if not root.is_dir():
            continue
        match = _find_by_name(root, set(names), EXECUTABLE_SEARCH_MAX_DEPTH)
        if match:
            cache["executables"][cache_key] = str(match)
            return match
    cache["executables"].pop(cache_key, None)
    return None


def find_rom(rom_filename: str, roms_dir: Path, cache: dict) -> Path | None:
    """Same caching approach as find_executable, keyed on roms_dir too so
    changing it in the Manager doesn't risk returning a stale path."""
    cache_key = f"{rom_filename}::{roms_dir}"
    cached = cache["roms"].get(cache_key)
    if cached and Path(cached).is_file():
        return Path(cached)

    if not roms_dir.is_dir():
        return None
    match = _find_by_name(roms_dir, {rom_filename}, ROM_SEARCH_MAX_DEPTH)
    if match:
        cache["roms"][cache_key] = str(match)
        return match
    cache["roms"].pop(cache_key, None)
    return None


def build_pc_launch_args(profile: dict, executable: Path, rom_path: Path | None) -> list[str]:
    """The exact CLI argv a PC-side emulator gets launched with, shared with
    try_launch_args.py so that checker exercises the same logic a real
    launch does rather than a separate reimplementation that could drift.

    Every emulator here except RPCS3 takes its rom as a trailing positional
    argument after any flags, RPCS3's own CLI is the opposite (confirmed
    against its actual usage: the game path first, then its no-gui and
    fullscreen flags): the boot target has to come *before* those flags, or it parses
    as neither flag having a boot target at all ("Cannot run no-gui mode
    without boot target", confirmed live). rom_before_args, when a profile
    sets it, is the escape hatch for that rather than hardcoding RPCS3 as a
    special case here."""
    if rom_path and profile.get("rom_before_args"):
        return [str(executable), str(rom_path), *profile["pre_args"]]
    args = [str(executable), *profile["pre_args"]]
    if rom_path:
        args.append(str(rom_path))
    return args


def find_emulator_for_package(package: str, emulators: dict, rom_filename: str | None, android_core: str | None) -> dict | None:
    """Every package here (RetroArch aside) is single-system, so the
    package match alone already tells the whole story, no guessing from
    the ROM's extension involved, or needed, for any of those.

    RetroArch (com.retroarch) is the one exception: it's a multi-core,
    multi-console frontend on the Android side, and iiSU reports that same
    package for it regardless of which system the game actually is. But
    iiSU also always reports *which core it actually launched with*,
    the intent's LIBRETRO extra (android_core), and that's a strictly
    better signal than the ROM's file extension, which can be outright
    wrong: .chd is chdman's container for both PS1 CDs and Dreamcast GD-
    ROMs, and plenty of consoles here share .zip/.7z, so guessing the
    console from extension alone is guessing at something android_core
    already just told us. android_core is trusted first, unconditionally,
    whenever it's present, not just for a curated subset of extensions,
    which also means any core this project has never explicitly curated
    (e.g. an arcade/MAME core resolved for a .zip) still gets routed
    correctly instead of falling through to a guess.

    A resolved core first checks RETROARCH_CORE_OVERRIDES, cores with
    their own dedicated, better-suited standalone emulator PC-side (e.g.
    Flycast over RetroArch-with-flycast-core for Dreamcast), confirmed
    live as necessary: a Dreamcast .gdi launched com.retroarch with
    LIBRETRO=flycast_libretro_android.so even with standalone Flycast
    picked in iiSU. Failing that override, the core is passed straight
    through to a generic RetroArch launch (-L cores/<core>.dll), so
    long as that core is actually present on the Windows RetroArch
    install (ensure_retroarch_core, below, downloads it from the
    libretro buildbot if it isn't), rather than substituted into one of
    this module's own curated per-extension templates, so this isn't
    limited to consoles/cores someone has explicitly added here.

    Only when android_core is missing entirely (or doesn't look like a
    libretro-android core filename at all) does this fall back to the
    plain extension-based guess in RETROARCH_BY_EXTENSION/
    RETROARCH_SAFETY_NET_EXTENSIONS, a reasonable default for the rare
    case iiSU doesn't report a core, never the primary mechanism."""
    for prefix, profile in emulators.items():
        if not package.startswith(prefix):
            continue
        if "by_extension" in profile:
            by_ext = profile["by_extension"]

            if android_core:
                core_dll = retroarch_core_dll_for_android_core(android_core)
                if core_dll:
                    override = standalone_profile_for_core_dll(core_dll)
                    if override:
                        return override
                    return {"exe_names": ["retroarch.exe"], "pre_args": ["-L", f"cores/{core_dll}", "-f"]}

            ext = Path(rom_filename).suffix.lower() if rom_filename else None
            return by_ext.get(ext) if ext else None
        return profile
    return None


def core_dll_from_pre_args(pre_args: list[str]) -> str | None:
    for arg in pre_args:
        # config.json's pre_args are always written with "cores/..." (see
        # shared/emulator_defaults.py); normalize a literal backslash
        # first since Path.name on Linux's PurePosixPath doesn't treat
        # "\\" as a separator at all, unlike Path.name on Windows.
        normalized = arg.replace("\\", "/")
        if normalized.startswith("cores/"):
            return normalized.rsplit("/", 1)[-1]
    return None


def ensure_retroarch_core(retroarch_dir: Path, core_dll: str) -> bool:
    """RetroArch loads its core list from disk, not from anything iiSU or
    this bridge tracks, a core this project's own config.json expects
    (e.g. fceumm_libretro.dll for NES) can easily not actually be there if
    it was never downloaded through RetroArch's own Online Updater. RetroArch
    doesn't error visibly when that happens: it just fails to load the core
    and exits straight back to iiSU, which from the PC side looks
    indistinguishable from nothing happening at all. Downloads the missing
    core from libretro's own official nightly buildbot (the same binaries
    RetroArch's in-app updater itself pulls from) instead of leaving that
    silent failure to happen. Returns True if the core is present by the
    time this returns (already there, or freshly downloaded), False if it
    couldn't be obtained, the caller still attempts the launch either way,
    since a download failure here shouldn't be worse than today's silent
    RetroArch exit."""
    core_path = retroarch_dir / "cores" / core_dll
    if core_path.is_file():
        return True

    url = LIBRETRO_CORE_URL.format(core_dll=core_dll)
    print(f"[bridge] {core_dll} isn't installed, downloading it from the libretro buildbot...")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            zip_bytes = resp.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            data = z.read(core_dll)
    except Exception as e:
        print(f"[bridge] couldn't download {core_dll} ({e}), the game launch will likely fail")
        return False

    core_path.parent.mkdir(parents=True, exist_ok=True)
    core_path.write_bytes(data)
    print(f"[bridge] installed {core_dll}")
    return True


def friendly_emulator_name(executable: Path) -> str:
    """Human-readable name for the handoff overlay's status line (e.g.
    "Waiting on DuckStation..." instead of "Waiting on duckstation-qt-x64-
    ReleaseLTCG.exe..."). Falls back to the executable's own filename,
    still readable, just less polished, for anything not in this
    project's curated list, e.g. some other RetroArch-compatible fork."""
    for app_label, exe_names in all_emulator_exe_names():
        if executable.name in exe_names:
            return app_label
    return executable.stem
