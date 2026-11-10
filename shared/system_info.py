"""A few lines describing the PC and this install, written at the top of the
start and Setup logs. Most "it is slow" and "it won't start" reports turn on
facts the reporter has to be asked for afterwards: how much memory and disk
is free, how many processors, where it is installed and on what kind of path.
Having them in the log answers that before it is asked.

Every reading is best effort and never raises: a log line that can't be
produced is simply left out.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GB = 1024 ** 3


def memory_gb() -> tuple[float, float] | None:
    """(total, available) physical memory in GB."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.ullTotalPhys / GB, status.ullAvailPhys / GB
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                values[key] = int(rest.split()[0]) * 1024
        return values["MemTotal"] / GB, values["MemAvailable"] / GB
    except Exception:  # noqa: BLE001; a log line, never worth failing over
        return None


def disk_free_gb(path: Path = PROJECT_ROOT) -> tuple[float, float] | None:
    """(free, total) GB on the drive holding `path`."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return usage.free / GB, usage.total / GB


def system_name(system: str | None = None, release: str | None = None, version: str | None = None) -> str:
    """"Windows 11", "Linux 6.8.0". Windows 11 still calls itself version 10
    and is told apart by its build number, 22000 and up, so a log read on
    its own would otherwise name the wrong Windows."""
    system = platform.system() if system is None else system
    release = platform.release() if release is None else release
    version = platform.version() if version is None else version
    if system == "Windows" and release == "10":
        try:
            if int(version.split(".")[2]) >= 22000:
                release = "11"
        except (IndexError, ValueError):
            pass
    return f"{system} {release}"


def summary_lines(install_root: Path = PROJECT_ROOT) -> list[str]:
    """Ready-to-print lines, without a prefix."""
    lines = []
    try:
        lines.append(f"System: {system_name()} ({platform.version()}), {platform.machine()}")
    except Exception:  # noqa: BLE001
        pass
    parts = []
    if os.cpu_count():
        parts.append(f"{os.cpu_count()} logical processors")
    memory = memory_gb()
    if memory:
        parts.append(f"{memory[0]:.0f} GB memory, {memory[1]:.1f} GB free")
    if parts:
        lines.append("Hardware: " + ", ".join(parts))
    disk = disk_free_gb(install_root)
    where = f"Install folder: {install_root}"
    if disk:
        where += f" ({disk[0]:.1f} GB free of {disk[1]:.0f} GB on its drive)"
    lines.append(where)
    return lines
