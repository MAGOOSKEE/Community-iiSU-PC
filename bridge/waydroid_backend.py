"""Drives Waydroid (Linux, Wayland sessions) as the Android runtime instead
of the SDK emulator. start_iisu_pc.py/stop_iisu_pc.py and the Setup wizard
call into here when shared.vm_backend.resolve_backend() says "waydroid".

How it maps onto the AVD flow it replaces:

- The container itself (waydroid-container.service) is a system service
  that needs root to start. This module never installs it or silently
  escalates; it checks, and where a one-click polkit prompt (pkexec) is
  enough to start it, it offers that, otherwise it raises WaydroidError
  with the exact command to run.
- `waydroid session start` is a long-lived foreground process, so it is
  launched detached with its output going to a log file, same as the AVD's
  emulator.exe (see start_iisu_pc._launch_once for why a pipe is wrong).
- Everything after that talks to Android over adb exactly like the AVD
  does: Waydroid's adbd listens on <container ip>:5555, `adb connect` makes
  it a normal device, and ANDROID_SERIAL pins it so a phone on USB can't
  make plain `adb` calls ambiguous.
- Resolution goes through Waydroid's persist.waydroid.width/height props
  (applied on session start), density through `wm density` once booted.
  Waydroid draws its UI as one fullscreen Wayland window by default, so
  none of the AVD's window-hiding/fullscreen handling applies.

Written against Waydroid's documented CLI; the pure parsing/ordering logic
lives in shared/vm_backend.py and is unit-tested, the subprocess glue here
is only exercised on a real Linux/Waydroid machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.platform_compat import detached_popen_kwargs, subprocess_creationflags
from shared.vm_backend import (
    WAYDROID_ADB_PORT,
    WAYDROID_INSTALL_DOCS,
    WaydroidInstallPlan,
    parse_waydroid_status,
    vm_device_serial,
    waydroid_address_candidates,
    waydroid_install_plan,
    waydroid_manual_install_hint,
)

WAYDROID_LOG_PATH = Path(__file__).parent / "waydroid.log"
LEASES_PATHS = (Path("/var/lib/misc/dnsmasq.waydroid0.leases"), Path("/var/lib/NetworkManager/dnsmasq-waydroid0.leases"))
SESSION_START_TIMEOUT = 90
BOOT_TIMEOUT = 240
CONTAINER_UNIT = "waydroid-container.service"

INSTALL_HINT = (
    "Install Waydroid from your distro's packages or https://docs.waydro.id/usage/install-on-desktops "
    "(e.g. `sudo apt install waydroid`, `sudo dnf install waydroid`, `yay -S waydroid`), "
    "then run Setup again."
)
BINDER_HINT = (
    "Your kernel has no binder support, which Waydroid needs. Use a kernel that includes it "
    "(linux, linux-lts, and linux-zen on Arch ship binder; on Debian/Ubuntu install the "
    "waydroid package's binder module or a kernel with CONFIG_ANDROID_BINDER_IPC), then reboot."
)


class WaydroidError(RuntimeError):
    pass


def _run(args: list[str], timeout: float = 30, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("creationflags", subprocess_creationflags())
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)


def _waydroid(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return _run(["waydroid", *args], timeout=timeout)


# == Preflight ==

def kernel_has_binder() -> bool:
    """binder is what the Android container's IPC runs on. It's either a
    loaded module (binder_linux), built into the kernel (shows in
    /proc/filesystems as binder via binderfs), or already mounted."""
    for probe in (Path("/proc/modules"), Path("/proc/filesystems")):
        try:
            if "binder" in probe.read_text(errors="replace"):
                return True
        except OSError:
            continue
    return Path("/dev/binderfs").exists() or Path("/dev/binder").exists()


def is_initialized() -> bool:
    """`waydroid init` writes its config here; no file means no images yet."""
    return Path("/var/lib/waydroid/waydroid.cfg").is_file()


def preflight() -> list[str]:
    """Human-readable blockers for starting Waydroid at all (empty list =
    good to go). Doesn't include "container not running": starting that is
    something start() can offer to do."""
    problems = []
    if shutil.which("waydroid") is None:
        problems.append(INSTALL_HINT)
        return problems
    if not kernel_has_binder():
        problems.append(BINDER_HINT)
    if not is_initialized():
        problems.append("Waydroid hasn't been initialized yet. Run `sudo waydroid init` (downloads the Android image), then try again.")
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"):
        problems.append("Waydroid only draws into a Wayland session, and this isn't one. Log into a Wayland session, or set IISUPC_VM_BACKEND=avd to use the Android emulator instead.")
    return problems


# == Status ==

def status() -> dict[str, str]:
    try:
        result = _waydroid("status", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_waydroid_status(result.stdout)


def container_running() -> bool:
    return status().get("container", "").upper() in ("RUNNING", "FROZEN")


def session_running() -> bool:
    return status().get("session", "").upper() == "RUNNING"


def is_running() -> bool:
    """True when the session is up and adb can see the VM, the Waydroid
    equivalent of start_iisu_pc.is_avd_running()."""
    if not session_running():
        return False
    try:
        result = _run(["adb", "devices"], timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return vm_device_serial(result.stdout) is not None


# == Container / session lifecycle ==

def ensure_container_running() -> None:
    if container_running():
        return
    if shutil.which("pkexec") and shutil.which("systemctl"):
        print("[waydroid] the Waydroid container isn't running, asking permission to start it...")
        result = _run(["pkexec", "systemctl", "start", CONTAINER_UNIT], timeout=120)
        if result.returncode == 0:
            deadline = time.time() + 20
            while time.time() < deadline:
                if container_running():
                    return
                time.sleep(1)
    raise WaydroidError(
        "The Waydroid container isn't running. Start it with `sudo systemctl enable --now "
        f"{CONTAINER_UNIT}` (or `sudo waydroid container start`), then try again."
    )


def apply_display(display: dict) -> None:
    """Pushes the configured resolution into Waydroid's own props. They
    persist and take effect when the session starts, so this runs right
    before every start. Failure is logged and ignored: a wrong resolution
    is a cosmetic problem, not a reason to refuse to start."""
    for prop, key in (("persist.waydroid.width", "width"), ("persist.waydroid.height", "height")):
        value = display.get(key)
        if not value:
            continue
        try:
            result = _waydroid("prop", "set", prop, str(int(value)), timeout=20)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            print(f"[waydroid] couldn't set {prop} ({exc}), continuing")
            continue
        if result.returncode != 0:
            print(f"[waydroid] `waydroid prop set {prop}` failed: {(result.stderr or result.stdout).strip()}")


def _start_session_process() -> subprocess.Popen:
    log_file = open(WAYDROID_LOG_PATH, "wb")
    try:
        return subprocess.Popen(
            ["waydroid", "session", "start"],
            **detached_popen_kwargs(),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
    finally:
        log_file.close()


def read_neighbour_and_lease_text() -> tuple[str, str]:
    leases = ""
    for path in LEASES_PATHS:
        try:
            leases += path.read_text(errors="replace") + "\n"
        except OSError:
            continue
    neigh = ""
    try:
        neigh = _run(["ip", "-4", "neigh", "show", "dev", "waydroid0"], timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return leases, neigh


def connect_adb(configured_address: str | None = None, timeout: float = 60) -> str:
    """Connects adb to the container and returns the serial. Tries each
    candidate address (see shared.vm_backend.waydroid_address_candidates)
    until one shows up as a connected device."""
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        leases, neigh = read_neighbour_and_lease_text()
        for address in waydroid_address_candidates(leases, neigh, configured_address):
            target = f"{address}:{WAYDROID_ADB_PORT}"
            try:
                connect = _run(["adb", "connect", target], timeout=10)
                last_output = (connect.stdout + connect.stderr).strip()
                devices = _run(["adb", "devices"], timeout=10)
            except (OSError, subprocess.SubprocessError) as exc:
                last_output = str(exc)
                continue
            serial = vm_device_serial(devices.stdout)
            if serial:
                return serial
        time.sleep(2)
    raise WaydroidError(
        "Couldn't connect adb to Waydroid. The container is up but nothing answered on port "
        f"{WAYDROID_ADB_PORT} (last adb output: {last_output or 'none'}). Open Waydroid's Settings > "
        "System > Developer options and turn on USB debugging, or set \"waydroid_adb_address\" in "
        "config.json to the IP shown under Settings > About."
    )


def wait_for_boot(timeout: float = BOOT_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = _run(["adb", "shell", "getprop", "sys.boot_completed"], timeout=10)
            if result.stdout.strip() == "1":
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(2)
    return False


def apply_density(display: dict) -> None:
    density = display.get("density")
    if not density:
        return
    try:
        _run(["adb", "shell", "wm", "density", str(int(density))], timeout=15)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass


def start(config: dict) -> str:
    """Brings Waydroid up and returns the adb serial. Idempotent: an
    already-running session is reused. Raises WaydroidError with an
    actionable message for anything the user has to fix themselves."""
    problems = preflight()
    if problems:
        raise WaydroidError("\n".join(problems))
    ensure_container_running()

    display = config.get("display", {})
    if not session_running():
        apply_display(display)
        print("[waydroid] starting the session...")
        process = _start_session_process()
        deadline = time.time() + SESSION_START_TIMEOUT
        while time.time() < deadline and not session_running():
            if process.poll() is not None and not session_running():
                tail = ""
                try:
                    tail = "\n".join(WAYDROID_LOG_PATH.read_text(errors="replace").splitlines()[-15:])
                except OSError:
                    pass
                raise WaydroidError(f"`waydroid session start` exited (code {process.returncode}) before the session came up.\n{tail}")
            time.sleep(1)
        if not session_running():
            raise WaydroidError(f"The Waydroid session didn't start within {SESSION_START_TIMEOUT}s, see {WAYDROID_LOG_PATH}.")
        # Draws Android's UI as a window on the Wayland compositor; without
        # it the session runs invisibly.
        subprocess.Popen(
            ["waydroid", "show-full-ui"], **detached_popen_kwargs(), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
    else:
        print("[waydroid] session already running.")

    serial = connect_adb(config.get("waydroid_adb_address"))
    os.environ["ANDROID_SERIAL"] = serial
    print(f"[waydroid] adb connected ({serial}), waiting for Android to finish booting...")
    if not wait_for_boot():
        raise WaydroidError(f"Android in Waydroid didn't finish booting within {BOOT_TIMEOUT}s.")
    apply_density(display)
    return serial


def stop() -> None:
    """Disconnects adb and ends the session. The container service is left
    running (it's a system service, and restarting it needs root again)."""
    try:
        _run(["adb", "disconnect"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        _waydroid("session", "stop", timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
    deadline = time.time() + 20
    while time.time() < deadline and session_running():
        time.sleep(1)


def install_apk(apk_path: Path) -> None:
    """adb install, falling back to Waydroid's own installer if adb isn't
    reachable (e.g. USB debugging off in the container)."""
    result = _run(["adb", "install", "-r", str(apk_path)], timeout=300)
    if result.returncode == 0 and "Success" in result.stdout:
        return
    fallback = _waydroid("app", "install", str(apk_path), timeout=300)
    if fallback.returncode != 0:
        raise WaydroidError(
            f"Couldn't install {apk_path.name} into Waydroid.\nadb: {(result.stdout + result.stderr).strip()}\n"
            f"waydroid: {(fallback.stdout + fallback.stderr).strip()}"
        )


def initialize() -> None:
    """One-time `waydroid init` (downloads the Android image, needs root).
    Goes through a polkit prompt so no terminal is needed; where that isn't
    available the user is told the exact command instead."""
    if shutil.which("pkexec") is None:
        raise WaydroidError("Waydroid isn't initialized. Run `sudo waydroid init` in a terminal, then run Setup again.")
    result = _run(["pkexec", "waydroid", "init"], timeout=3600)
    if result.returncode != 0 or not is_initialized():
        raise WaydroidError(
            "`waydroid init` didn't finish. Run `sudo waydroid init` in a terminal to see why, then run Setup again.\n"
            f"{(result.stderr or result.stdout).strip()[-400:]}"
        )


def read_os_release() -> str:
    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        try:
            return path.read_text(errors="replace")
        except OSError:
            continue
    return ""


def install_plan_for_this_machine() -> WaydroidInstallPlan | None:
    """The distro-package install for this machine, or None if there isn't a
    safe unattended one (or pkexec, needed to ask for permission, is missing)."""
    if shutil.which("pkexec") is None:
        return None
    return waydroid_install_plan(read_os_release())


def manual_install_hint() -> str:
    return waydroid_manual_install_hint(read_os_release())


def install_waydroid(plan: WaydroidInstallPlan) -> None:
    """Runs the plan's package-manager command through a polkit prompt. Only
    ever called after the user opted in (the Setup dialog, or the
    IISUPC_INSTALL_WAYDROID env var for headless runs); never on its own."""
    print(f"[waydroid] installing with: {plan.display} (asks for administrator permission)")
    result = _run(plan.elevated_command, timeout=1800)
    output = (result.stdout + result.stderr).strip()
    if output:
        print("\n".join(f"[waydroid]   {line}" for line in output.splitlines()[-15:]))
    if result.returncode != 0 or shutil.which("waydroid") is None:
        raise WaydroidError(
            f"`{plan.display}` didn't install Waydroid (exit code {result.returncode}). "
            f"Your distro's repositories may not carry it, see {WAYDROID_INSTALL_DOCS} for the official install steps, "
            "then run Setup again."
        )
    print("[waydroid] Waydroid installed.")
