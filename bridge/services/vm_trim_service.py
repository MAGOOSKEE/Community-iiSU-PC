"""Applies the "lean VM" profile (shared/vm_trim.py) to a running Android VM
over adb: installs LeanKeyboard as the default keyboard, removes the packages
iiSU doesn't need, checks the VM is still healthy after each tier, and can put
everything back.

Every function takes an `adb` callable, adb(*args, timeout=30) returning an
object with .returncode/.stdout/.stderr, so the logic is testable with a fake
and usable against any device (the default VM, or a scratch one given a serial).
"""

from __future__ import annotations

import hashlib
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shared import vm_trim

Adb = Callable[..., "subprocess.CompletedProcess[str]"]
Progress = Callable[[str], None]


class TrimError(Exception):
    pass


@dataclass
class TierResult:
    tier: str
    removed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # health problems found afterwards
    rolled_back: bool = False


@dataclass
class TrimReport:
    keyboard: str = "not requested"
    tiers: list[TierResult] = field(default_factory=list)
    # True when the VM no longer boots after a change. That can't be repaired
    # in place (no package manager without a booted system), so the caller has
    # to rebuild the VM's data, which is cheap on a fresh install.
    needs_rebuild: bool = False

    @property
    def removed_count(self) -> int:
        return sum(len(t.removed) for t in self.tiers if not t.rolled_back)

    def summary(self) -> str:
        parts = [f"keyboard: {self.keyboard}"]
        if self.needs_rebuild:
            parts.append("the VM no longer boots and must be rebuilt")
        for tier in self.tiers:
            if tier.rolled_back:
                parts.append(f"{tier.tier}: rolled back ({'; '.join(tier.problems)})")
            else:
                note = f", {len(tier.failed)} could not be removed" if tier.failed else ""
                parts.append(f"{tier.tier}: {len(tier.removed)} removed{note}")
        return " | ".join(parts)


def _text(result) -> str:
    return (getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")


# == Reading the VM ==

def installed_packages(adb: Adb) -> set[str]:
    return vm_trim.parse_package_list(_text(adb("shell", "pm list packages", timeout=60)))


def removed_for_user0(adb: Adb) -> set[str]:
    """Packages present on the image but uninstalled for user 0."""
    all_known = vm_trim.parse_package_list(_text(adb("shell", "pm list packages -u", timeout=60)))
    return all_known - installed_packages(adb)


# == LeanKeyboard ==

def download_pinned(pin: vm_trim.PinnedApk, dest: Path, progress: Progress = lambda _m: None) -> Path:
    """Downloads the pinned APK and refuses anything whose SHA-256 differs from
    the pin. Reuses a previous download that still verifies."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    def digest(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    if dest.is_file() and digest(dest) == pin.sha256:
        return dest
    progress(f"downloading {pin.package} {pin.version}...")
    request = urllib.request.Request(pin.url, headers={"User-Agent": "Community-iiSU-PC"})
    temp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(request, timeout=60) as response, open(temp, "wb") as out:
        for chunk in iter(lambda: response.read(1 << 20), b""):
            out.write(chunk)
    if digest(temp) != pin.sha256:
        temp.unlink(missing_ok=True)
        raise TrimError(f"The downloaded {pin.package} didn't match its pinned checksum, so it was discarded.")
    temp.replace(dest)
    return dest


KEYBOARD_ATTEMPTS = 4


def install_keyboard(adb: Adb, apk: Path, progress: Progress = lambda _m: None, sleep=time.sleep) -> tuple[bool, str]:
    """Installs LeanKeyboard, enables it and makes it the default. Returns
    (ok, detail): ok only if the VM then reports it as the default keyboard,
    detail says what went wrong otherwise. Android registers a freshly
    installed keyboard a moment after the install returns (a live run saw
    `ime set` miss it), so enabling and checking are retried."""
    progress("installing LeanKeyboard...")
    installed = _text(adb("install", "-r", str(apk), timeout=300))
    if not vm_trim.is_success(installed):
        reason = installed.strip().splitlines()[-1][:120] if installed.strip() else "no output"
        return False, f"the install failed ({reason})"
    current = ""
    for attempt in range(KEYBOARD_ATTEMPTS):
        if attempt:
            sleep(3)
        for command in vm_trim.ime_commands():
            adb("shell", *command, timeout=30)
        current = _text(adb("shell", "settings get secure default_input_method", timeout=30))
        if vm_trim.LEAN_KEYBOARD_IME in current:
            return True, ""
    return False, f"Android kept {current.strip() or 'no keyboard'} as the default"


# == Health ==

def health_problems(adb: Adb, baseline_crashes: set[str] | None = None) -> list[str]:
    """What looks wrong with the VM right now. Empty list means healthy. Crashes
    already present in `baseline_crashes` (from before the change) are ignored."""
    problems = []
    if _text(adb("shell", "getprop sys.boot_completed", timeout=30)).strip() != "1":
        problems.append("Android isn't fully booted")
    if not _text(adb("shell", "pidof com.android.systemui", timeout=30)).strip():
        problems.append("System UI isn't running")
    crashed = crashed_packages(adb) - (baseline_crashes or set())
    # An app we just removed may still show an old crash; only system-critical ones matter.
    critical = sorted(p for p in crashed if vm_trim.is_protected(p) and p != "com.iisulauncher")
    if critical:
        problems.append("crashes in " + ", ".join(critical))
    return problems


def crashed_packages(adb: Adb) -> set[str]:
    import re

    return set(re.findall(r"Process: ([\w.]+)", _text(adb("shell", "logcat -b crash -d -v brief", timeout=60))))


# == Applying ==

def apply_tier(adb: Adb, tier: str, keyboard_ready: bool, progress: Progress = lambda _m: None) -> TierResult:
    result = TierResult(tier)
    plan = vm_trim.removal_plan(installed_packages(adb), tiers=(tier,), keyboard_ready=keyboard_ready)
    for index, package in enumerate(plan, start=1):
        output = _text(adb("shell", " ".join(vm_trim.uninstall_args(package)), timeout=60))
        if vm_trim.is_success(output):
            result.removed.append(package)
        else:
            result.failed[package] = output.strip().splitlines()[-1][:120] if output.strip() else "no output"
        if index % 10 == 0:
            progress(f"{tier}: {index}/{len(plan)}")
    return result


def restore_packages(adb: Adb, packages: list[str]) -> list[str]:
    """Puts packages back. Returns the ones that could not be restored."""
    failed = []
    for package in packages:
        if not vm_trim.restore_ok(_text(adb("shell", " ".join(vm_trim.restore_args(package)), timeout=60))):
            failed.append(package)
    return failed


# Android writes package changes to disk about 10 seconds after they happen.
# Shut the VM down sooner (Setup does, right after installing) and the change
# is silently lost; found live when a whole tier "succeeded" and then came back
# after a reboot. This gives it comfortable margin.
PERSIST_SECONDS = 20
BOOT_TIMEOUT = 240


def get_fingerprint(adb: Adb) -> str:
    return _text(adb("shell", "getprop ro.build.fingerprint", timeout=30)).strip()


class EmulatorExited(RuntimeError):
    """The emulator process itself closed during a reboot check."""

    def __init__(self):
        super().__init__("The Android emulator closed unexpectedly while checking the VM still starts.")


def reboot_and_wait(
    adb: Adb, timeout: int = BOOT_TIMEOUT, sleep=time.sleep, clock=time.monotonic, alive: Callable[[], bool] | None = None,
) -> bool:
    """Reboots the guest and waits for Android to finish booting. False if it
    doesn't within the timeout (a boot loop).

    alive, when given, says whether the emulator process is still running. If
    it closes, EmulatorExited is raised right away: that is the emulator
    crashing, not Android failing to boot. Seen live in Setup with Emulator
    37.2.13, on a reboot after a tier that removed nothing; eight reboots in
    the same mode, trimmed and not, came back fine, so it's an occasional
    emulator crash. Setup waited out the whole timeout, then threw away the
    trim as if it had broken the VM."""
    adb("reboot", timeout=30)
    sleep(10)
    deadline = clock() + timeout
    while clock() < deadline:
        if alive is not None and not alive():
            raise EmulatorExited()
        try:
            if _text(adb("shell", "getprop sys.boot_completed", timeout=15)).strip() == "1":
                return True
        except subprocess.TimeoutExpired:
            pass
        sleep(3)
    return False


def _rebooted(adb: Adb, sleep, alive, restart, progress: Progress) -> bool:
    """reboot_and_wait, given one more go in a fresh emulator when the first
    closes or Android doesn't come back in time. Only a VM that still won't
    boot after that counts as broken by the change."""
    try:
        if reboot_and_wait(adb, timeout=BOOT_TIMEOUT, sleep=sleep, alive=alive):
            return True
        why = "Android didn't come back in time"
    except EmulatorExited:
        if restart is None:
            raise
        why = "the emulator closed during the reboot"
    if restart is None:
        return False
    progress(f"{why}, starting the emulator again to check...")
    return restart()


def make_lean(
    adb: Adb,
    keyboard_apk: Path | None = None,
    tiers: tuple[str, ...] | None = None,
    progress: Progress = lambda _m: None,
    verify_reboot: bool = True,
    sleep=time.sleep,
    alive: Callable[[], bool] | None = None,
    restart: Callable[[], bool] | None = None,
) -> TrimReport:
    """The whole profile. Keyboard first (so Gboard can go), then one tier at a
    time. Each tier is given time to persist and, by default, verified across a
    reboot, because some dependencies only show up at boot. A tier that leaves
    the VM unhealthy is rolled back; one that stops it booting at all flags the
    report as needing a rebuild and ends the run.

    alive and restart let the caller that runs the emulator handle it closing
    during a reboot, or Android not coming back in time (see reboot_and_wait):
    restart stops it, starts it again and returns whether Android booted. If
    it did, the check carries on; if not, the VM needs a rebuild. Without
    restart, a closed emulator raises EmulatorExited.

    tiers=None picks them from the image build: the full profile only on builds
    it has been verified on, just the low-risk user-facing apps otherwise."""
    report = TrimReport()
    if tiers is None:
        tiers = vm_trim.tiers_for_build(get_fingerprint(adb))
    keyboard_ready = False
    if keyboard_apk is not None:
        keyboard_ready, why = install_keyboard(adb, keyboard_apk, progress, sleep)
        report.keyboard = "installed and set as default" if keyboard_ready else f"{why}, Gboard kept"
    baseline = crashed_packages(adb)

    for tier in tiers:
        progress(f"removing the {tier} tier...")
        result = apply_tier(adb, tier, keyboard_ready, progress)
        report.tiers.append(result)
        progress("letting Android save the change...")
        sleep(PERSIST_SECONDS)
        if verify_reboot:
            progress("rebooting to check the VM still starts...")
            if not _rebooted(adb, sleep, alive, restart, progress):
                result.problems = ["Android did not boot after the change"]
                report.needs_rebuild = True
                return report
        result.problems = health_problems(adb, baseline)
        if result.problems:
            restore_packages(adb, result.removed)
            sleep(PERSIST_SECONDS)
            result.rolled_back = True
            break
    return report
