"""Per-game launch overrides: change which PC emulator one game uses, add
command-line flags or environment variables for just that game, or run a
command (set a display mode, start a helper) right before it launches.

Stored in config.json under "game_overrides", keyed by the ROM's file name,
which is what the launch bridge already has in hand when iiSU asks for a
game:

    "game_overrides": {
        "Final Fantasy VII.m3u": {
            "emulator": "com.github.stenzek.duckstation",
            "extra_args": "-fullscreen -nogui",
            "env": "SDL_VIDEODRIVER=windows",
            "pre_launch": "C:\\tools\\set_refresh_rate.bat 60"
        }
    }

Everything is optional; an override with nothing set is the same as none.
Overrides are read fresh on every launch (like the rest of config.json), so
editing one needs no restart.

Pure logic only, apart from run_pre_launch: unit-testable without a bridge.
"""

from __future__ import annotations

import copy
import os
import shlex
import subprocess
from collections.abc import Mapping

from emulator_profiles import parse_env, split_extra_args

KEYS = ("emulator", "extra_args", "env", "pre_launch")
PRE_LAUNCH_TIMEOUT = 30  # seconds


def normalize_override(raw: Mapping | None) -> dict:
    """Only known keys, as stripped strings. Unknown keys are dropped so a
    hand-edited config can't smuggle anything else into a launch."""
    raw = raw or {}
    return {key: str(raw.get(key) or "").strip() for key in KEYS}


def is_empty(override: Mapping | None) -> bool:
    return not any(normalize_override(override).values())


def get_override(config: Mapping, rom_filename: str | None) -> dict | None:
    """The normalized override for this ROM file name, or None if there isn't
    a meaningful one. Exact match first, then case-insensitive, since the
    ROM library lives on a case-insensitive filesystem on Windows."""
    if not rom_filename:
        return None
    overrides = config.get("game_overrides")
    if not isinstance(overrides, Mapping):
        return None
    raw = overrides.get(rom_filename)
    if raw is None:
        folded = rom_filename.casefold()
        raw = next((value for key, value in overrides.items() if isinstance(key, str) and key.casefold() == folded), None)
    if not isinstance(raw, Mapping):
        return None
    override = normalize_override(raw)
    return None if is_empty(override) else override


def set_override(config: dict, rom_filename: str, override: Mapping | None) -> dict:
    """Returns config with the override stored (or removed when empty). Any
    differently-cased existing key for the same file is replaced rather than
    left behind to shadow the new value."""
    updated = dict(config)
    overrides = {
        key: value
        for key, value in (config.get("game_overrides") or {}).items()
        if not (isinstance(key, str) and key.casefold() == rom_filename.casefold())
    }
    normalized = normalize_override(override)
    if not is_empty(normalized):
        overrides[rom_filename] = normalized
    if overrides:
        updated["game_overrides"] = overrides
    else:
        updated.pop("game_overrides", None)
    return updated


def selectable_emulators(emulators: Mapping) -> list[str]:
    """Emulator entries a game can be redirected to: the ones that name a
    single executable. A by_extension entry (RetroArch) needs a core chosen
    per file type, which a one-game override can't express."""
    return [prefix for prefix, profile in emulators.items() if isinstance(profile, Mapping) and "exe_names" in profile]


def apply_to_profile(profile: Mapping, override: Mapping, emulators: Mapping, is_windows: bool) -> tuple[dict, list[str]]:
    """The profile to launch with after applying the override, plus notes
    for the launch log. The emulator override swaps in another emulator's
    profile (when it exists and is selectable); extra_args are appended to
    that profile's own pre_args, which build_pc_launch_args already places
    correctly relative to the ROM for either argument order."""
    result = copy.deepcopy(dict(profile))
    notes: list[str] = []

    target = override.get("emulator", "")
    if target:
        if target in selectable_emulators(emulators):
            result = copy.deepcopy(dict(emulators[target]))
            notes.append(f"emulator {target}")
        else:
            notes.append(f"emulator override '{target}' isn't a configured single-executable emulator, ignored")

    extra = split_extra_args(override.get("extra_args", ""), is_windows)
    if extra:
        result["pre_args"] = [*result.get("pre_args", []), *extra]
        notes.append(f"extra args {extra}")
    return result, notes


def launch_env(override: Mapping, base: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Environment for the emulator process, or None to inherit unchanged
    (so overrides with no env never alter how the process is spawned)."""
    extra = parse_env(override.get("env", ""))
    if not extra:
        return None
    env = dict(os.environ if base is None else base)
    env.update(extra)
    return env


def run_pre_launch(command: str, is_windows: bool, timeout: float = PRE_LAUNCH_TIMEOUT) -> tuple[bool, str]:
    """Runs the pre-launch command and waits for it (so it can, say, change
    a display mode before the emulator starts). Never raises and never
    blocks past the timeout: a broken helper must not stop a game from
    launching. Returns (ok, message) for the launch log."""
    command = (command or "").strip()
    if not command:
        return True, ""
    try:
        args = shlex.split(command, posix=not is_windows)
        if is_windows:
            args = [a[1:-1] if len(a) >= 2 and a[0] == a[-1] == '"' else a for a in args]
        if not args:
            return True, ""
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"pre-launch command timed out after {timeout:.0f}s, launching anyway"
    except (OSError, ValueError) as exc:
        return False, f"pre-launch command couldn't run ({exc}), launching anyway"
    if result.returncode != 0:
        return False, f"pre-launch command exited {result.returncode}, launching anyway"
    return True, "pre-launch command ran"
