"""Turns what adb can see of the Android VM's storage into the categories the
Settings > Storage page draws: iiSU's title art, iiSU itself, Google's apps,
other apps, other files, Android itself, and free space.

adb can't read other apps' private data (no root), so the numbers come from
two places that work for the shell user:

- `dumpsys diskstats`, which reports every package's code, data and cache
  size once Android's disk-stats job has run (the service forces it), and
- `du` over the shared-storage folders (/sdcard), which the shell can read.

Whatever `df` says is used but neither source accounts for is Android's own
files (system updates, runtime caches), shown as "Android system".

Pure functions only, so the arithmetic is unit-tested without a VM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from shared.vm_storage import StorageUsage

IISU_PACKAGE = "com.iisulauncher"

# Packages that make up Google Play and what it depends on (plus Chrome,
# which the lean profile keeps). Matched by prefix.
GOOGLE_PREFIXES = (
    "com.google.android.gms",
    "com.google.android.gsf",
    "com.android.vending",
    "com.google.android.webview",
    "com.google.android.trichromelibrary",
    "com.android.chrome",
    "com.google.android.play",
    "com.google.android.finsky",
)

# (key, label, colour). Order is the order of the bar and the legend.
CATEGORIES = (
    ("iisu_art", "iiSU title art and media", "#71e0ff"),
    ("iisu_app", "iiSU app and its data", "#8258fa"),
    ("roms", "ROM placeholders", "#f0a030"),
    ("google", "Google Play, services and Chrome", "#4fd67a"),
    ("apps", "Other apps", "#5e84ff"),
    ("files", "Other files (downloads, photos, ...)", "#ff7aa8"),
    ("system", "Android system and updates", "#7a7a82"),
    ("free", "Free", "#2a2a2e"),
)
COLORS = {key: color for key, _label, color in CATEGORIES}
LABELS = {key: label for key, label, _color in CATEGORIES}

# Shared-storage folders measured with `du`: key -> path.
SHARED_PATHS = {
    "iisu_media": f"/sdcard/Android/media/{IISU_PACKAGE}",
    "roms": "/sdcard/Roms",
    "sdcard": "/sdcard",
}


@dataclass(frozen=True)
class PackageSize:
    app_bytes: int = 0
    data_bytes: int = 0
    cache_bytes: int = 0

    @property
    def total_kb(self) -> int:
        return (self.app_bytes + self.data_bytes + self.cache_bytes) // 1024


@dataclass(frozen=True)
class Segment:
    key: str
    label: str
    color: str
    kb: int
    note: str = ""


@dataclass
class Breakdown:
    usage: StorageUsage
    segments: list[Segment] = field(default_factory=list)
    itemised: bool = True  # False when per-app sizes weren't available
    top_apps: list[tuple[str, int]] = field(default_factory=list)  # (package, kb), biggest first

    def segment(self, key: str) -> Segment | None:
        return next((s for s in self.segments if s.key == key), None)


def parse_diskstats(text: str) -> dict[str, PackageSize] | None:
    """Per-package sizes from `dumpsys diskstats`, or None if the dump has no
    per-app section (the job hasn't run yet)."""
    arrays: dict[str, list] = {}
    for key in ("Package Names", "App Sizes", "App Data Sizes", "Cache Sizes"):
        match = re.search(rf"^{key}: (\[.*\])\s*$", text, re.MULTILINE)
        if not match:
            return None
        try:
            arrays[key] = json.loads(match.group(1))
        except ValueError:
            return None
    names = arrays["Package Names"]
    result = {}
    for index, name in enumerate(names):
        def at(key: str) -> int:
            values = arrays[key]
            return int(values[index]) if index < len(values) else 0

        result[name] = PackageSize(at("App Sizes"), at("App Data Sizes"), at("Cache Sizes"))
    return result


def parse_du(output: str) -> dict[str, int]:
    """`du -sk` output as path -> kilobytes. Unreadable paths are left out."""
    sizes = {}
    for line in output.splitlines():
        match = re.match(r"^(\d+)\s+(\S.*)$", line.strip())
        if match:
            sizes[match.group(2)] = int(match.group(1))
    return sizes


def is_google_package(package: str) -> bool:
    return package.startswith(GOOGLE_PREFIXES)


def build_breakdown(
    usage: StorageUsage,
    packages: dict[str, PackageSize] | None,
    shared_kb: dict[str, int],
) -> Breakdown:
    """`shared_kb` is keyed by SHARED_PATHS' keys. Anything missing counts as 0."""
    iisu_art = shared_kb.get("iisu_media", 0)
    roms = shared_kb.get("roms", 0)
    other_files = max(0, shared_kb.get("sdcard", 0) - iisu_art - roms)

    iisu_app = google = apps = 0
    top: list[tuple[str, int]] = []
    if packages is not None:
        for name, size in packages.items():
            if name == IISU_PACKAGE:
                iisu_app += size.total_kb
            elif is_google_package(name):
                google += size.total_kb
            else:
                apps += size.total_kb
            top.append((name, size.total_kb))
        top.sort(key=lambda item: item[1], reverse=True)

    known = iisu_art + iisu_app + roms + google + apps + other_files
    system = max(0, usage.used_kb - known)
    amounts = {
        "iisu_art": iisu_art, "iisu_app": iisu_app, "roms": roms, "google": google,
        "apps": apps, "files": other_files, "system": system, "free": usage.avail_kb,
    }
    segments = []
    for key, label, color in CATEGORIES:
        note = ""
        if key == "system" and packages is None:
            # Without per-app sizes every app's share lands here.
            label, note = "Apps and Android system", "per-app sizes weren't available"
        segments.append(Segment(key, label, color, amounts[key], note))
    return Breakdown(usage, segments, itemised=packages is not None, top_apps=top[:8])


def format_kb(kb: int) -> str:
    if kb >= 1024 * 1024:
        return f"{kb / 1024 / 1024:.1f} GB"
    if kb >= 1024:
        return f"{kb / 1024:.0f} MB"
    return f"{kb} KB"


def bar_fractions(segments: list[Segment]) -> list[tuple[Segment, float]]:
    """Each segment's share of the bar, normalised by the segments' own sum so
    rounding in the sources (known usage can slightly exceed df's) can't push
    the bar past its end."""
    total = sum(s.kb for s in segments)
    if total <= 0:
        return [(s, 0.0) for s in segments]
    return [(s, s.kb / total) for s in segments]
