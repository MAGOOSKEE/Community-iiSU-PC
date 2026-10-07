"""Named, switchable Android emulator launch configurations ("profiles"),
so audio glitches, tearing, or slow UI can be A/B tested by changing one
setting and restarting, instead of hand-editing config files.

A profile only holds knobs that apply at launch to the one AVD this project
already set up (GPU backend, hardware acceleration, CPU cores, RAM, audio
devices, extra emulator flags, extra environment variables). It never
touches which system image the AVD runs; that's a separate AVD. Profiles
live in config.json under "emulator_profiles":

    {"active": "Default", "profiles": {"Default": {...}, "Host GPU": {...}}}

An install from before profiles existed has no such key, so get_profiles()
synthesizes a "Default" from the old display.gpu_mode setting and nothing
else changes for it.

Everything here is pure (no subprocess, no filesystem) so it's unit-testable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shlex
from collections.abc import Mapping

DEFAULT_PROFILE_NAME = "Default"

GPU_MODES = ("auto", "host", "swiftshader_indirect", "angle_indirect")
ACCEL_MODES = ("auto", "on", "off")
# default: leave the AVD's audio alone. no_input: keep sound output but
# detach the host microphone (a frequent source of crackle/latency on
# Windows). none: no audio at all, to prove audio is/isn't the culprit.
AUDIO_MODES = ("default", "no_input", "none")

DEFAULT_PROFILE: dict = {
    "gpu_mode": "auto",
    "accel": "auto",
    "cores": 0,  # 0 = whatever the AVD is configured with
    "ram_mb": 0,  # 0 = whatever the AVD is configured with
    "audio": "default",
    "extra_args": "",
    "env": "",
}

PRESETS: dict[str, dict] = {
    "Default": {},
    "Host GPU": {"gpu_mode": "host"},
    "Software GPU (SwiftShader)": {"gpu_mode": "swiftshader_indirect"},
    "ANGLE GPU (DirectX on Windows)": {"gpu_mode": "angle_indirect"},
    "Audio test: no microphone": {"audio": "no_input"},
    "Audio test: no audio": {"audio": "none"},
    "More resources (6 cores, 4 GB)": {"cores": 6, "ram_mb": 4096},
}


def _as_int(value, default: int = 0, low: int = 0, high: int = 1_000_000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def normalize_profile(raw: Mapping | None) -> dict:
    """Fills in defaults and clamps anything invalid, so a hand-edited or
    older config.json can never produce a bad emulator command line."""
    raw = raw or {}
    profile = copy.deepcopy(DEFAULT_PROFILE)
    if raw.get("gpu_mode") in GPU_MODES:
        profile["gpu_mode"] = raw["gpu_mode"]
    if raw.get("accel") in ACCEL_MODES:
        profile["accel"] = raw["accel"]
    if raw.get("audio") in AUDIO_MODES:
        profile["audio"] = raw["audio"]
    profile["cores"] = _as_int(raw.get("cores"), 0, 0, 64)
    profile["ram_mb"] = _as_int(raw.get("ram_mb"), 0, 0, 65536)
    profile["extra_args"] = str(raw.get("extra_args") or "").strip()
    profile["env"] = str(raw.get("env") or "").strip()
    return profile


def get_profiles(config: Mapping) -> tuple[str, dict[str, dict]]:
    """(active profile name, {name: normalized profile}). Always returns at
    least one profile and an active name that exists."""
    block = config.get("emulator_profiles")
    profiles: dict[str, dict] = {}
    active = DEFAULT_PROFILE_NAME
    if isinstance(block, Mapping):
        raw_profiles = block.get("profiles")
        if isinstance(raw_profiles, Mapping):
            for name, raw in raw_profiles.items():
                if isinstance(name, str) and name.strip():
                    profiles[name.strip()] = normalize_profile(raw if isinstance(raw, Mapping) else None)
        active = str(block.get("active") or DEFAULT_PROFILE_NAME)
    if not profiles:
        legacy_gpu = (config.get("display") or {}).get("gpu_mode")
        profiles[DEFAULT_PROFILE_NAME] = normalize_profile({"gpu_mode": legacy_gpu})
    if active not in profiles:
        active = DEFAULT_PROFILE_NAME if DEFAULT_PROFILE_NAME in profiles else next(iter(profiles))
    return active, profiles


def active_profile(config: Mapping) -> dict:
    active, profiles = get_profiles(config)
    return profiles[active]


def with_profiles(config: Mapping, active: str, profiles: Mapping[str, Mapping]) -> dict:
    """A copy of config with the profiles block replaced (normalized)."""
    updated = dict(config)
    updated["emulator_profiles"] = {
        "active": active,
        "profiles": {name: normalize_profile(profile) for name, profile in profiles.items()},
    }
    return updated


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE per line; blank lines and #comments skipped; bad lines
    ignored rather than failing a launch over a typo."""
    result: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and all(ch.isalnum() or ch == "_" for ch in key):
            result[key] = value.strip()
    return result


def split_extra_args(text: str, is_windows: bool) -> list[str]:
    """shlex with Windows-friendly (non-POSIX) quoting rules so a path with
    backslashes survives; a quoting mistake yields no extra args rather
    than an exception at launch time."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=not is_windows)
    except ValueError:
        return []
    if is_windows:
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    return parts


def build_emulator_args(profile: Mapping, is_windows: bool, kvm_present: bool = False) -> list[str]:
    """The emulator command-line fragment a profile contributes (after the
    -avd flag, before platform extras like -no-snapshot)."""
    profile = normalize_profile(profile)
    args = ["-gpu", profile["gpu_mode"]]

    if profile["accel"] != "auto":
        args += ["-accel", profile["accel"]]
    elif not is_windows and kvm_present:
        # Unchanged from before profiles: use KVM whenever it's there.
        args += ["-accel", "on"]

    if profile["cores"]:
        args += ["-cores", str(profile["cores"])]
    if profile["ram_mb"]:
        args += ["-memory", str(profile["ram_mb"])]
    if profile["audio"] == "none":
        args.append("-no-audio")

    args += split_extra_args(profile["extra_args"], is_windows)
    return args


def config_ini_overrides(profile: Mapping) -> dict[str, str]:
    """AVD config.ini keys a profile controls. Written on every launch (not
    only when non-default) so switching back to Default restores them."""
    profile = normalize_profile(profile)
    return {
        "hw.audioInput": "no" if profile["audio"] in ("no_input", "none") else "yes",
        "hw.audioOutput": "no" if profile["audio"] == "none" else "yes",
    }


def fingerprint(profile: Mapping) -> str:
    """Stable hash of the parts of a profile that change what hardware the
    guest sees, so a quickboot snapshot taken under a different profile
    isn't resumed."""
    normalized = normalize_profile(profile)
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode("utf-8")).hexdigest()


def describe(profile: Mapping) -> str:
    """One line for logs: only what differs from stock."""
    profile = normalize_profile(profile)
    bits = [f"gpu={profile['gpu_mode']}"]
    if profile["accel"] != "auto":
        bits.append(f"accel={profile['accel']}")
    if profile["cores"]:
        bits.append(f"{profile['cores']} cores")
    if profile["ram_mb"]:
        bits.append(f"{profile['ram_mb']} MB")
    if profile["audio"] != "default":
        bits.append(f"audio={profile['audio']}")
    if profile["extra_args"]:
        bits.append(f"extra: {profile['extra_args']}")
    if profile["env"]:
        bits.append("custom env")
    return ", ".join(bits)
