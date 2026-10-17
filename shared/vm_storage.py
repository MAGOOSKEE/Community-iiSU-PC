"""Sizing and reading the Android VM's internal storage.

The SDK's default AVD profile gives the data partition 6 GB. That holds the
Play-image system updates and app data AND iiSU's downloaded artwork (about
1 MB per game), and fills up: iiSU then dies with "SQLiteFullException:
database or disk is full" and its ROM indexer fails with ENOSPC. New AVDs
are created bigger; the partition can't be grown in place (Android won't
expand an existing data partition), so an existing install has to have its
data image recreated.

Pure functions only (no adb, no filesystem beyond the one config.ini edit),
so they're unit-testable and usable from both the installer and the bridge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_PARTITION_GB = 16
DATA_PARTITION_KEY = "disk.dataPartition.size"

# Free-space thresholds for the warning states.
CRITICAL_FREE_KB = 200 * 1024
LOW_FREE_FRACTION = 0.10


@dataclass(frozen=True)
class StorageUsage:
    total_kb: int
    used_kb: int
    avail_kb: int

    @property
    def used_percent(self) -> int:
        return round(100 * self.used_kb / self.total_kb) if self.total_kb else 0


def parse_size_kb(text: str) -> int | None:
    """Android's df -h sizes ("5.8G", "50M", "0", "123K") as kilobytes."""
    match = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([KMGT]?)\s*", text)
    if not match:
        return None
    value = float(match.group(1))
    factor = {"": 1 / 1024, "K": 1, "M": 1024, "G": 1024**2, "T": 1024**3}[match.group(2)]
    return int(value * factor)


def parse_df(output: str) -> StorageUsage | None:
    """The data partition's usage from `adb shell df -h /data` output (the
    last line that has the Filesystem Size Used Avail Use% layout)."""
    for line in reversed(output.strip().splitlines()):
        parts = line.split()
        if len(parts) >= 5 and parts[4].endswith("%") and parts[0] != "Filesystem":
            total, used, avail = (parse_size_kb(parts[i]) for i in (1, 2, 3))
            if None not in (total, used, avail):
                return StorageUsage(total, used, avail)
    return None


def usage_level(usage: StorageUsage) -> str:
    """"ok", "low" (under 10% free) or "critical" (under 200 MB free: iiSU
    starts crashing and Android can't even update apps)."""
    if usage.avail_kb < CRITICAL_FREE_KB:
        return "critical"
    if usage.total_kb and usage.avail_kb / usage.total_kb < LOW_FREE_FRACTION:
        return "low"
    return "ok"


def describe(usage: StorageUsage) -> str:
    def gb(kb: int) -> str:
        return f"{kb / 1024 / 1024:.1f} GB"

    return f"{gb(usage.used_kb)} used of {gb(usage.total_kb)} ({usage.used_percent}%), {gb(usage.avail_kb)} free"


def parse_configured_gb(config_ini_text: str) -> float | None:
    """The data partition size an AVD's config.ini asks for, in GB."""
    for line in config_ini_text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == DATA_PARTITION_KEY:
            kb = parse_size_kb(value.strip().rstrip("Bb"))
            return None if kb is None else kb / 1024 / 1024
    return None


def set_data_partition_size(config_ini: Path, gb: int = DEFAULT_DATA_PARTITION_GB) -> bool:
    """Sets the AVD's data partition size, replacing an existing entry or
    adding one. Returns True if the file changed. Only effective for an AVD
    whose userdata image doesn't exist yet: the emulator never resizes an
    existing one, so callers check that first."""
    wanted = f"{DATA_PARTITION_KEY}={gb}G"
    try:
        text = config_ini.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = text.splitlines()
    new_lines, seen = [], False
    for line in lines:
        if line.partition("=")[0].strip() == DATA_PARTITION_KEY:
            new_lines.append(wanted)
            seen = True
        else:
            new_lines.append(line)
    if not seen:
        new_lines.append(wanted)
    updated = "\n".join(new_lines) + "\n"
    if updated == text:
        return False
    config_ini.write_text(updated, encoding="utf-8")
    return True
