"""What the PC and the Android emulator say about hardware acceleration, for
the start and Setup logs.

Whether the VM is fast, slow or won't start at all comes down to a handful
of facts: is virtualization on in the firmware, is a hypervisor active, which
accelerator did the emulator pick, did it get the real graphics card. A log
that already holds them answers most "it is slow" reports without a round of
questions. Nothing here changes anything: it only asks and reads.

Three sources:
  - the system (Windows' own inventory, or /dev/kvm on Linux),
  - the emulator's own `-accel-check`,
  - emulator.log, where the emulator records what it actually used.

The first two take a second or two each, so FeatureProbe asks on a background
thread while the VM boots and the start waits for nothing. Every reading is
best effort: one that can't be had is left out, never raised.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_WINDOWS_QUERY = (
    "$c = Get-CimInstance Win32_Processor | Select-Object -First 1; "
    "$s = Get-CimInstance Win32_ComputerSystem; "
    '"$($c.Name)|$($c.NumberOfCores)|$($c.VirtualizationFirmwareEnabled)|$($s.HypervisorPresent)"'
)


def _run(args: list[str], timeout: float) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "") + (result.stderr or "")


def parse_windows_query(output: str) -> dict:
    """The fields of _WINDOWS_QUERY's one line of output."""
    for line in output.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) == 4:
            return {
                "processor": " ".join(parts[0].split()),
                "cores": parts[1],
                "firmware_virtualization": parts[2].lower() == "true",
                "hypervisor_present": parts[3].lower() == "true",
            }
    return {}


def system_lines() -> list[str]:
    """The processor and the virtualization state as the system reports it."""
    if IS_WINDOWS:
        info = parse_windows_query(_run(["powershell", "-NoProfile", "-Command", _WINDOWS_QUERY], 20))
        if not info:
            return []
        lines = []
        if info["processor"]:
            lines.append(f"Processor: {info['processor']}" + (f", {info['cores']} cores" if info["cores"] else ""))
        lines.append(describe_windows_virtualization(info["firmware_virtualization"], info["hypervisor_present"]))
        return lines
    kvm = Path("/dev/kvm")
    if not kvm.exists():
        return ["Virtualization: /dev/kvm is missing, so KVM is unavailable (off in the BIOS/UEFI, or the kvm module isn't loaded)."]
    if not os.access(kvm, os.R_OK | os.W_OK):
        return ["Virtualization: /dev/kvm exists but this user can't open it (usually: not in the kvm group)."]
    return ["Virtualization: KVM is available (/dev/kvm is usable)."]


def describe_windows_virtualization(firmware_enabled: bool, hypervisor_present: bool) -> str:
    """Windows hides the firmware flag from itself once its own hypervisor is
    running, so "hypervisor active" already means virtualization is on, and
    the firmware flag only says something when no hypervisor is."""
    if hypervisor_present:
        return "Virtualization: a hypervisor is active (Hyper-V / Windows Hypervisor Platform), so hardware virtualization is on."
    if firmware_enabled:
        return "Virtualization: enabled in the firmware, but no hypervisor is active (Windows Hypervisor Platform is off)."
    return "Virtualization: not enabled in the firmware (Intel VT-x or AMD-V/SVM is off in the BIOS/UEFI), and no hypervisor is active."


def parse_accel_check(output: str) -> str | None:
    """`emulator -accel-check` prints "accel:", a status number, a sentence,
    then "accel". Returns "the sentence (status N)"."""
    lines = [line.strip() for line in _ANSI_RE.sub("", output).splitlines() if line.strip()]
    try:
        start = lines.index("accel:")
    except ValueError:
        return None
    body = [line for line in lines[start + 1:] if line != "accel"]
    if len(body) < 2:
        return None
    return f"{body[1]} (status {body[0]})"


def accel_check_line(emulator_exe: Path) -> str | None:
    if not Path(emulator_exe).is_file():
        return None
    answer = parse_accel_check(_run([str(emulator_exe), "-accel-check"], 30))
    return f"Emulator acceleration check: {answer}" if answer else None


def emulator_log_lines(text: str) -> list[str]:
    """What the emulator recorded in emulator.log about what it is using:
    its version, the accelerator, graphics, memory, and how it booted."""
    text = _ANSI_RE.sub("", text)

    def find(pattern: str) -> str | None:
        match = re.search(pattern, text)
        return match.group(1).strip() if match else None

    lines = []
    version = find(r"Android emulator version ([\d.]+)")
    accelerator = None
    if re.search(r"WHPX on Windows|Windows Hypervisor Platform accelerator", text):
        accelerator = "Windows Hypervisor Platform (WHPX)"
    elif re.search(r"\bAEHD\b|Android Emulator Hypervisor Driver", text):
        accelerator = "Android Emulator Hypervisor Driver (AEHD)"
    elif re.search(r"\bKVM\b", text):
        accelerator = "KVM"
    elif re.search(r"\bHVF\b|Hypervisor\.Framework", text):
        accelerator = "Hypervisor.framework"
    if accelerator and "accelerator is operational" in text:
        accelerator += ", operational"
    parts = [f"version {version}" if version else None, f"accelerator {accelerator}" if accelerator else None]
    parts = [part for part in parts if part]
    if parts:
        lines.append("Emulator: " + ", ".join(parts))
    elif version is None and text.strip():
        lines.append("Emulator: no accelerator reported in its log (it may be running without hardware acceleration).")

    graphics = []
    backend = find(r"Graphics backend: (\S+)")
    if backend:
        graphics.append(backend)
    modes = re.search(r"vulkan_mode_selected:(\S+) gles_mode_selected:(\S+)", text)
    if modes:
        graphics.append(f"OpenGL ES {modes.group(2)}, Vulkan {modes.group(1)}")
    adapter = find(r"Graphics Adapter Android Emulator OpenGL ES Translator \(([^)]+)\)") or find(r"Selecting Vulkan device: ([^,\n]+)")
    if adapter:
        graphics.append(adapter)
    if graphics:
        lines.append("Emulator graphics: " + ", ".join(graphics))

    details = []
    ram = find(r"Increasing RAM size to (\d+)MB")
    if ram:
        details.append(f"memory raised to {ram} MB")
    loaded = find(r"Successfully loaded snapshot '[^']*' using (\d+) ms")
    if loaded:
        details.append(f"resumed from its snapshot in {loaded} ms")
    elif "Loading snapshot" in text:
        details.append("loading its snapshot")
    if details:
        lines.append("Emulator: " + ", ".join(details))
    return lines


def emulator_log_lines_from(path: Path) -> list[str]:
    try:
        return emulator_log_lines(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


class FeatureProbe:
    """Asks the system and the emulator about acceleration on a background
    thread. start() returns at once; lines() gives whatever has come back,
    waiting at most `timeout` seconds for the rest."""

    def __init__(self, emulator_exe: Path | None):
        self._emulator_exe = emulator_exe
        self._lines: list[str] = []
        self._thread: threading.Thread | None = None

    def start(self) -> "FeatureProbe":
        self._thread = threading.Thread(target=self._ask, name="feature-probe", daemon=True)
        self._thread.start()
        return self

    def _ask(self) -> None:
        try:
            self._lines.extend(system_lines())
            if self._emulator_exe is not None:
                line = accel_check_line(self._emulator_exe)
                if line:
                    self._lines.append(line)
        except Exception:  # noqa: BLE001; a log line, never worth failing a start over
            pass

    def lines(self, timeout: float = 5.0) -> list[str]:
        if self._thread is not None:
            self._thread.join(timeout)
        return list(self._lines)
