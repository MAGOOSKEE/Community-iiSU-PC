"""
Tears down everything start_iisu_pc.py starts (the launch bridge and the
AVD), plus anything else standing in the way of a clean slate. This is
what runs when you click Stop on the Manager's Home page.

Tries a graceful `adb emu kill` first and gives qemu a few seconds to exit
on its own, a clean exit is what lets it release its own lock files
(hardware-qemu.ini.lock, multiinstance.lock) under the portable AVD's own
directory (android-sdk-portable/avd-home/<name>.avd/, see portable_sdk.py).
Skipping straight to a force-kill leaves those locks behind and the *next*
start then fails immediately with "emulator.exe exited early (code 1)", so
force-taskkill is only a fallback for whatever the graceful path doesn't
manage to stop in time, and even then, any leftover lock files are swept
away afterward so the next start isn't blocked by them.

Also stops the adb server (it's a persistent background process that
outlives the AVD it was talking to and never exits on its own).

Deliberately does NOT touch any snapshot state left behind by the
shutdown itself: start_iisu_pc.py's boot-fingerprint check decides on
the *next* start whether that saved snapshot is still trustworthy (a
quick resume) or stale (a fresh cold boot, which naturally overwrites
it on exit). Clearing it here unconditionally would defeat quick resume
before it ever got a chance to be used. For the same reason a graceful
shutdown is given time to finish writing that snapshot, see
wait_for_emulator_exit().

Every sweep matches this install's own folder, so stopping one install
never takes down another install's VM or bridge.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

# The embeddable Python runtime this app ships with does not auto-add a
# launched script's own directory to sys.path the way a normal Python
# install does. This script is launched as a raw script path by both
# uninstall.py's own Stop (in-process, unaffected) and, more importantly,
# launch_bridge.py's quit-hotkey/quit-chord handling
# (subprocess.Popen([sys.executable, str(STOP_SCRIPT)]), not "-m"), where
# the bare import below would otherwise fail with ModuleNotFoundError on
# a real embeddable-Python install (see the identical, confirmed-live fix
# in launch_bridge.py itself).
sys.path.insert(0, str(Path(__file__).resolve().parent))
# The project root too: portable_sdk and the rest import the shared/ package, which
# sits next to bridge/, not inside it, and a raw script launch only gets its own folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portable_sdk import PORTABLE_AVD_HOME, PORTABLE_SDK
from shared import adb_client, app_version
from shared.platform_compat import IS_WINDOWS, subprocess_creationflags
from shared.vm_backend import BACKEND_WAYDROID, resolve_backend, vm_device_connected

# adb/taskkill/powershell are all console-subsystem executables; this
# script itself always runs from the GUI (pythonw.exe), which has no
# console for them to inherit, so each would otherwise pop up its own.
# (Windows-only concern: subprocess_creationflags() is 0 on Linux.)
CREATE_NO_WINDOW = subprocess_creationflags()

STATE_PATH = Path(__file__).parent / ".runtime_state.json"
CONFIG_PATH = Path(__file__).parent / "config.json"
GRACEFUL_STOP_TIMEOUT = 10  # seconds
# adb loses the VM within a couple of seconds of `adb emu kill`, but the emulator
# keeps running while it writes its quick-resume snapshot: usually a few seconds
# more, once about 20 (both measured on the 2.5 GB VM Setup creates). Killing it
# sooner leaves a snapshot the next start can't load, so the start cold boots.
SNAPSHOT_SAVE_TIMEOUT = 90  # seconds

# This install's own emulator and bridge, by path. Matching only
# "android-sdk-portable" or "launch_bridge.py" also hit every other install's.
INSTALL_EMULATOR_NEEDLE = str(PORTABLE_SDK)
INSTALL_BRIDGE_NEEDLE = str(Path(__file__).resolve().parent / "launch_bridge.py")
# qemu is the process that writes the snapshot. netsimd and crashpad_handler run
# from the same SDK folder but can outlive it, so the save wait watches qemu alone.
INSTALL_QEMU_NEEDLE = str(PORTABLE_SDK / "emulator" / "qemu")


def load_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def load_avd_name(state: dict) -> str | None:
    if "avd_name" in state:
        return state["avd_name"]
    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get("avd_name")
    return None


def is_avd_running() -> bool:
    return vm_device_connected(adb_client.devices_text())


def load_config_safely() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def stop_waydroid() -> None:
    """Ends the Waydroid session (and its adb connection). The container is
    a system service and is deliberately left running. Failures are
    reported, not raised: Stop must still go on to kill the bridge."""
    try:
        import waydroid_backend

        print("[stop] stopping the Waydroid session...")
        waydroid_backend.stop()
    except Exception as e:  # noqa: BLE001; Stop must keep going to tear down the bridge
        print(f"[stop] couldn't stop Waydroid cleanly ({e})")


def kill_tree(pid: int) -> None:
    # Linux path relies on pid being its own process group leader
    # (start_new_session=True at spawn time, see
    # shared.platform_compat.detached_popen_kwargs(), used for both
    # bridge_pid and emulator_pid), os.killpg() on a pid that shares a
    # process group with this script would kill this script too. Verified
    # live: correct (kills only the target) with start_new_session=True,
    # kills the caller as well without it.
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        return
    import os
    import signal
    try:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(0.2)
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.2)
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass  # already gone


def kill_by_cmdline_match(needle: str) -> None:
    """Force-kills any process whose command line contains `needle`, via
    PowerShell/WMI (wmic itself is deprecated/removed on newer Windows
    builds). This is the fallback for launch_bridge.py that bridge_pid
    alone can't cover: start_iisu_pc.py only ever records bridge_pid when
    *it* started the bridge process, if it instead found one already
    running (port already open, e.g. left over from a previous session
    that didn't get a clean Stop) it skips straight past that assignment,
    so the freshly-written state file has no bridge_pid at all and this
    script's PID-based kill above silently has nothing to kill. Matching
    on "launch_bridge.py" specifically (never a bare python.exe sweep,
    which would also take down unrelated Python processes on the same
    PC) makes this self-healing regardless of how state.json got out of
    sync with what's actually running. Callers pass this install's own path
    (INSTALL_BRIDGE_NEEDLE, INSTALL_EMULATOR_NEEDLE)."""
    if not IS_WINDOWS:
        subprocess.run(["pkill", "-f", needle], capture_output=True)
        return
    script = _matching_processes(needle) + " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)


def _matching_processes(needle: str) -> str:
    """A PowerShell pipeline of the processes whose command line contains
    needle, compared literally (no wildcards) and ignoring case, as Windows
    paths are. The PowerShell process itself is left out: needle is part of
    its own command line, so it would always count itself and could stop
    itself before reaching the rest."""
    literal = needle.lower().replace("'", "''")
    return (
        "Get-CimInstance Win32_Process "
        "| Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine "
        f"-and $_.CommandLine.ToLowerInvariant().Contains('{literal}') }}"
    )


def count_processes_matching(needle: str) -> int:
    if not IS_WINDOWS:
        result = subprocess.run(["pgrep", "-f", "-c", needle], capture_output=True, text=True)
        return int(result.stdout.strip() or 0)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"@({_matching_processes(needle)}).Count"],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def wait_for_emulator_exit(timeout: float = SNAPSHOT_SAVE_TIMEOUT) -> float | None:
    """After a graceful shutdown request, waits for this install's emulator to
    exit on its own, which is when its quick-resume snapshot is complete.
    Returns the seconds it took, or None if it was still running at timeout."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if count_processes_matching(INSTALL_QEMU_NEEDLE) == 0:
            return time.monotonic() - started
        time.sleep(1)
    return None


def _remove_path_with_retry(path: Path, attempts: int = 5, delay: float = 1.0) -> None:
    """A process that just got taskkilled doesn't always release its file
    handle the instant it exits, Windows can hold a lock file for a
    moment longer, which raises PermissionError if removal is attempted
    immediately. Retrying briefly avoids that for what's normally a
    sub-second timing gap, without ever blocking indefinitely if something
    is genuinely still holding it."""
    for attempt in range(attempts):
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
            return
        except OSError:
            if attempt == attempts - 1:
                print(f"[stop] couldn't remove {path.name}, leaving it, the next start will retry")
                return
            time.sleep(delay)


def clear_stale_locks(avd_name: str | None) -> None:
    if not avd_name:
        return
    avd_dir = PORTABLE_AVD_HOME / f"{avd_name}.avd"
    if not avd_dir.is_dir():
        return
    for lock_path in avd_dir.glob("*.lock"):
        print(f"[stop] clearing stale lock {lock_path.name}...")
        if lock_path.is_dir():
            for child in lock_path.iterdir():
                _remove_path_with_retry(child)
            _remove_path_with_retry(lock_path)
        else:
            _remove_path_with_retry(lock_path)


def main() -> None:
    started = time.monotonic()
    state = load_state()
    avd_name = load_avd_name(state)
    # What actually ran last time wins over what config would pick now, so
    # a session started under one backend is always stopped by that one.
    backend = state.get("backend") or resolve_backend(load_config_safely())
    print(f"[stop] {app_version.banner('stop', f'backend {backend}')}")

    if backend == BACKEND_WAYDROID:
        stop_waydroid()
    elif is_avd_running():
        print("[stop] asking the AVD to shut down gracefully...")
        asked = time.monotonic()
        subprocess.run(["adb", "emu", "kill"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        deadline = time.time() + GRACEFUL_STOP_TIMEOUT
        while time.time() < deadline and is_avd_running():
            time.sleep(0.25)
        if is_avd_running():
            print(f"[stop] the AVD was still running after {GRACEFUL_STOP_TIMEOUT}s, killing it...")
        else:
            print(f"[stop] the AVD shut down in {time.monotonic() - asked:.1f}s, waiting for it to save its quick-resume snapshot...")
            saved_in = wait_for_emulator_exit()
            if saved_in is None:
                print(f"[stop] the emulator was still saving after {SNAPSHOT_SAVE_TIMEOUT}s, stopping it (the next start will cold boot).")
            else:
                print(f"[stop] snapshot saved, the emulator exited {time.monotonic() - asked:.1f}s after the request.")

    bridge_pid = state.get("bridge_pid")
    if bridge_pid is not None:
        print(f"[stop] killing bridge_pid {bridge_pid}...")
        kill_tree(bridge_pid)

    emulator_pid = state.get("emulator_pid")
    if emulator_pid is not None and backend != BACKEND_WAYDROID and is_avd_running():
        print(f"[stop] killing emulator_pid {emulator_pid}...")
        kill_tree(emulator_pid)

    # Fallback sweep in case graceful shutdown didn't finish in time, the
    # state file is stale/missing, or a process got reparented away from
    # the PID we originally tracked (emulator.exe in particular tends to
    # leave a second shim process behind). Both sweeps below match on
    # command line rather than bare image name: state.json can have no
    # bridge_pid to fall back on at all (see kill_by_cmdline_match), and
    # taskkill by image name alone can't safely target "emulator.exe" or
    # "qemu-system-x86_64.exe" without also risking a real Android Studio
    # emulator instance someone has open for unrelated app development, or
    # another qemu-based tool entirely (e.g. WSL2), every process this
    # project launches runs out of this install's own android-sdk-portable/,
    # and matching that full path scopes the sweep to this install alone.
    print("[stop] sweeping for any orphaned launch_bridge.py process...")
    kill_by_cmdline_match(INSTALL_BRIDGE_NEEDLE)
    if backend != BACKEND_WAYDROID:
        print("[stop] sweeping for any remaining emulator/qemu process for this AVD...")
        kill_by_cmdline_match(INSTALL_EMULATOR_NEEDLE)

    # adb.exe runs as a persistent background server (any `adb` command
    # spawns it if it isn't already running) and never exits on its own
    # just because the AVD it was talking to did, left alone, it stays
    # running indefinitely after every single Stop. `adb kill-server` is
    # the documented graceful shutdown for it, unlike taskkill against the
    # other two processes above.
    print("[stop] stopping the adb server...")
    subprocess.run(["adb", "kill-server"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)

    if backend != BACKEND_WAYDROID:
        clear_stale_locks(avd_name)

    if STATE_PATH.is_file():
        STATE_PATH.unlink()

    print(f"[stop] Done in {time.monotonic() - started:.0f}s. Bridge and Android VM should both be fully stopped now.")


if __name__ == "__main__":
    main()
