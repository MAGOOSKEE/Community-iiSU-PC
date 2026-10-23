"""Behind Settings > Storage: what is using the Android VM's internal storage,
how big its file is on the host, cleaning up old copies, and recreating the VM at a different size.

Reading the VM goes through an `adb` callable, adb(*args, timeout=30), that
returns an object with .stdout/.stderr/.returncode, so the logic is testable
with a fake. Everything that changes files takes the paths it works on.

Recreating the VM is the only way to change its size: Android can't grow an
existing data partition, and a qcow2 file can't be made smaller than what the
guest has touched (measured: deleting 1.5 GB inside the VM, trimming, and
rewriting the file with qemu-img leaves it the same size). resize() therefore backs up what adb can reach, swaps the
data files for empty ones at the new size, reinstalls iiSU and puts the
backup back, and undoes the swap if the reinstall fails. The old data is kept
beside the AVD until the person deletes it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from shared import vm_storage, vm_storage_breakdown as bd

BRIDGE_DIR = Path(__file__).resolve().parent.parent
AVD_HOME = BRIDGE_DIR / "android-sdk-portable" / "avd-home"

DATA_IMAGE = "userdata-qemu.img.qcow2"
Adb = Callable[..., "subprocess.CompletedProcess[str]"]
Progress = Callable[[str], None]

DISKSTATS_JOB = re.compile(r"JOB #1000/(\d+):.*DiskStatsLoggingService")
DISKSTATS_WAIT_SECONDS = 60


class StorageError(Exception):
    pass


def _text(result) -> str:
    return (getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")


# == What is using the guest's storage ==

def _refresh_diskstats(adb: Adb, sleep, clock) -> str:
    """Android only computes per-app sizes when its disk-stats job runs (about
    daily, while idle and charging). Forces it, then waits for fresh numbers.
    Returns the final `dumpsys diskstats` text."""
    before = _text(adb("shell", "dumpsys diskstats", timeout=60))
    match = DISKSTATS_JOB.search(_text(adb("shell", "dumpsys jobscheduler | grep -m1 DiskStatsLoggingService", timeout=60)))
    if not match:
        return before
    adb("shell", f"cmd jobscheduler run -f android {match.group(1)}", timeout=30)
    deadline = clock() + DISKSTATS_WAIT_SECONDS
    latest = before
    while clock() < deadline:
        sleep(3)
        latest = _text(adb("shell", "dumpsys diskstats", timeout=60))
        if latest != before and bd.parse_diskstats(latest) is not None:
            break
    return latest


def collect_breakdown(adb: Adb, progress: Progress = lambda _m: None, sleep=time.sleep, clock=time.monotonic) -> bd.Breakdown:
    """The categorised breakdown of the running VM's data partition."""
    state = _text(adb("get-state", timeout=10)).strip()
    if state != "device":
        raise StorageError("The Android VM isn't running. Open Community-iiSU-PC first.")
    usage = vm_storage.parse_df_kb(_text(adb("shell", "df -k /data", timeout=30)))
    if usage is None:
        raise StorageError("Couldn't read the VM's storage usage.")

    progress("measuring iiSU's and your own files...")
    paths = bd.SHARED_PATHS
    du = bd.parse_du(_text(adb("shell", "du -sk " + " ".join(paths.values()) + " 2>/dev/null", timeout=300)))
    shared = {key: du.get(path, 0) for key, path in paths.items()}

    progress("asking Android for each app's size (this takes a few seconds)...")
    packages = bd.parse_diskstats(_refresh_diskstats(adb, sleep, clock))
    return bd.build_breakdown(usage, packages, shared)


# == The file on the host ==

def avd_dir(avd_name: str, avd_home: Path = AVD_HOME) -> Path:
    return avd_home / f"{avd_name}.avd"


def folder_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


@dataclass(frozen=True)
class OldCopy:
    path: Path
    size_bytes: int


@dataclass
class HostInfo:
    avd_dir: Path
    data_bytes: int  # the data image as it is on the drive right now
    configured_gb: float | None  # the size the VM was created with
    old_copies: list[OldCopy] = field(default_factory=list)

    @property
    def old_copies_bytes(self) -> int:
        return sum(copy.size_bytes for copy in self.old_copies)


def is_old_copy_name(name: str, avd_name: str) -> bool:
    """The folders earlier resizes (and the hand-made recovery steps in the
    README) leave next to the AVD."""
    return name.startswith((f"{avd_name}.pre-resize", f"{avd_name}.avd.bak"))


def find_old_copies(avd_name: str, avd_home: Path = AVD_HOME) -> list[OldCopy]:
    if not avd_home.is_dir():
        return []
    return [
        OldCopy(entry, folder_size(entry))
        for entry in sorted(avd_home.iterdir())
        if entry.is_dir() and is_old_copy_name(entry.name, avd_name)
    ]


def host_info(avd_name: str, avd_home: Path = AVD_HOME) -> HostInfo:
    directory = avd_dir(avd_name, avd_home)
    image = directory / DATA_IMAGE
    legacy = directory / "userdata-qemu.img"
    data_file = image if image.is_file() else legacy
    configured = None
    try:
        configured = vm_storage.parse_configured_gb((directory / "config.ini").read_text(encoding="utf-8"))
    except OSError:
        pass
    return HostInfo(
        directory,
        data_file.stat().st_size if data_file.is_file() else 0,
        configured,
        find_old_copies(avd_name, avd_home),
    )


def delete_old_copy(copy_path: Path, avd_name: str, avd_home: Path = AVD_HOME) -> None:
    """Deletes one old-copy folder. Refuses anything that isn't directly inside
    the AVD home with a name from is_old_copy_name, so a stray path can never
    take the live VM or anything else with it."""
    if copy_path.parent.resolve() != avd_home.resolve() or not is_old_copy_name(copy_path.name, avd_name):
        raise StorageError(f"{copy_path.name} isn't an old VM copy, so it was left alone.")
    shutil.rmtree(copy_path)


# == Recreating at a different size ==

class ResizeError(Exception):
    pass


@dataclass
class ResizeSteps:
    """The parts of a resize that touch a running VM, injected so the
    orchestration (ordering, rollback) is testable without one."""

    backup: Callable[[Path], object]  # VM running: write the iiSU data backup zip
    verify_backup: Callable[[Path], object]  # raises if the zip isn't restorable
    stop_vm: Callable[[], None]
    install: Callable[[Callable[[], None]], None]  # boot the fresh VM, install iiSU, call the hook while it's up
    restore: Callable[[Path], dict]  # VM running: put the backup back


@dataclass
class ResizeResult:
    new_gb: int
    old_copy: Path | None
    restore_outcome: dict = field(default_factory=dict)
    restore_error: str = ""


def resize(
    directory: Path,
    new_gb: int,
    backup_zip: Path,
    steps: ResizeSteps,
    progress: Progress = lambda _m: None,
    now: Callable[[], datetime] = datetime.now,
) -> ResizeResult:
    """Recreates the VM at new_gb. The order is the safety: nothing on disk
    changes until the backup exists and verifies; the old data files are
    moved aside (never deleted); and if the fresh VM can't be set up, the old
    files and size are put back."""
    from portable_sdk import DATA_FILES, reset_avd_data

    new_gb = vm_storage.clamp_storage_gb(new_gb)
    config_ini = directory / "config.ini"
    if not config_ini.is_file():
        raise ResizeError(f"{directory} isn't an Android VM folder (no config.ini).")

    progress("backing up iiSU's data from the VM...")
    steps.backup(backup_zip)
    try:
        steps.verify_backup(backup_zip)
    except Exception as e:  # noqa: BLE001; any problem here means the backup can't be trusted
        raise ResizeError(f"The backup didn't verify, so nothing was changed: {e}") from e

    progress("stopping the VM...")
    steps.stop_vm()

    aside = directory.parent / f"{directory.name.removesuffix('.avd')}.pre-resize-{now():%Y%m%d-%H%M%S}"
    original_config = config_ini.read_bytes()
    progress("moving the old data aside...")
    reset_avd_data(directory, aside=aside)
    (aside / "config.ini").write_bytes(original_config)
    vm_storage.set_data_partition_size(config_ini, new_gb)

    result = ResizeResult(new_gb, aside)

    def restore_hook() -> None:
        progress("putting iiSU's data back...")
        try:
            result.restore_outcome = steps.restore(backup_zip)
        except Exception as e:  # noqa: BLE001; the new VM is fine, report instead of undoing it
            result.restore_error = str(e)

    try:
        progress(f"creating the new {new_gb} GB VM and reinstalling iiSU (a few minutes)...")
        steps.install(restore_hook)
    except Exception as e:  # noqa: BLE001; undo the swap whatever went wrong
        progress("setup failed, putting the old VM back...")
        try:
            steps.stop_vm()  # a half-built VM may still be running and holding the files
        except Exception:  # noqa: BLE001; best effort, the file moves below are what matter
            pass
        reset_avd_data(directory)
        for name in DATA_FILES:
            if (aside / name).exists():
                shutil.move(str(aside / name), str(directory / name))
        config_ini.write_bytes(original_config)
        shutil.rmtree(aside, ignore_errors=True)
        raise ResizeError(f"The new VM couldn't be set up, so the old one was put back: {e}") from e
    return result


def real_steps(avd_name: str, lean: bool, apk: Path | None = None) -> ResizeSteps:
    """The live implementation of ResizeSteps (adb, the emulator, Setup's
    install code). Imported lazily: it pulls in the installer."""
    import sys

    from services import android_backup_service as backup_svc

    project_root = BRIDGE_DIR.parent
    for extra in (project_root, project_root / "installer"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    import setup_wizard
    import stop_iisu_pc

    work = setup_wizard.WORK_DIR
    candidates = [path for path in (work / "iisu-updated.apk", work / "iisu-patched.apk") if path.is_file()]
    patched = apk or (max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None)
    if patched is None:
        raise ResizeError("No patched iiSU was found to reinstall. Install or update iiSU once (Updates > iiSU App Updates) and try again.")

    def stop() -> None:
        try:
            stop_iisu_pc.main()
        except SystemExit:
            pass

    def install(after_install: Callable[[], None]) -> None:
        setup_wizard.rebuild_avd_with_iisu(avd_name, patched, lean=lean, after_install=after_install)

    return ResizeSteps(
        backup=backup_svc.create_backup,
        verify_backup=backup_svc.inspect_archive,
        stop_vm=stop,
        install=install,
        restore=backup_svc.restore_backup,
    )
