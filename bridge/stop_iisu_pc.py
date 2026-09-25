"""
Tears down everything start_iisu_pc.py starts (the launch bridge and the
AVD), plus anything else standing in the way of a clean slate. This is
what runs when you click Stop on manager.py's Home page.

Tries a graceful `adb emu kill` first and gives qemu a few seconds to exit
on its own -- a clean exit is what lets it release its own lock files
(hardware-qemu.ini.lock, multiinstance.lock) under the portable AVD's own
directory (android-sdk-portable/avd-home/<name>.avd/, see portable_sdk.py).
Skipping straight to a force-kill leaves those locks behind and the *next*
start then fails immediately with "emulator.exe exited early (code 1)", so
force-taskkill is only a fallback for whatever the graceful path doesn't
manage to stop in time -- and even then, any leftover lock files are swept
away afterward so the next start isn't blocked by them.

Also stops the adb server (it's a persistent background process that
outlives the AVD it was talking to and never exits on its own).

Deliberately does NOT touch any snapshot state left behind by the
shutdown itself: start_iisu_pc.py's boot-fingerprint check decides on
the *next* start whether that saved snapshot is still trustworthy (a
quick resume) or stale (a fresh cold boot, which naturally overwrites
it on exit). Clearing it here unconditionally would defeat quick resume
before it ever got a chance to be used.
"""

import json
import subprocess
import time
from pathlib import Path

from portable_sdk import PORTABLE_AVD_HOME

# adb/taskkill/powershell are all console-subsystem executables; this
# script itself always runs from the GUI (pythonw.exe), which has no
# console for them to inherit, so each would otherwise pop up its own.
CREATE_NO_WINDOW = 0x08000000

STATE_PATH = Path(__file__).parent / ".runtime_state.json"
CONFIG_PATH = Path(__file__).parent / "config.json"
GRACEFUL_STOP_TIMEOUT = 10  # seconds


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
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return any(line.startswith("emulator-") and "device" in line for line in result.stdout.splitlines())


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)


def kill_by_cmdline_match(needle: str) -> None:
    """Force-kills any process whose command line contains `needle`, via
    PowerShell/WMI (wmic itself is deprecated/removed on newer Windows
    builds). This is the fallback for launch_bridge.py that bridge_pid
    alone can't cover: start_iisu_pc.py only ever records bridge_pid when
    *it* started the bridge process -- if it instead found one already
    running (port already open, e.g. left over from a previous session
    that didn't get a clean Stop) it skips straight past that assignment,
    so the freshly-written state file has no bridge_pid at all and this
    script's PID-based kill above silently has nothing to kill. Matching
    on "launch_bridge.py" specifically (never a bare python.exe sweep,
    which would also take down unrelated Python processes on the same
    PC) makes this self-healing regardless of how state.json got out of
    sync with what's actually running."""
    script = (
        "Get-CimInstance Win32_Process "
        f"| Where-Object {{ $_.CommandLine -like '*{needle}*' }} "
        "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)


def _remove_path_with_retry(path: Path, attempts: int = 5, delay: float = 1.0) -> None:
    """A process that just got taskkilled doesn't always release its file
    handle the instant it exits -- Windows can hold a lock file for a
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
                print(f"[stop] couldn't remove {path.name}, leaving it -- the next start will retry")
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
    state = load_state()
    avd_name = load_avd_name(state)

    if is_avd_running():
        print("[stop] asking the AVD to shut down gracefully...")
        subprocess.run(["adb", "emu", "kill"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        deadline = time.time() + GRACEFUL_STOP_TIMEOUT
        while time.time() < deadline and is_avd_running():
            time.sleep(1)

    bridge_pid = state.get("bridge_pid")
    if bridge_pid is not None:
        print(f"[stop] killing bridge_pid {bridge_pid}...")
        kill_tree(bridge_pid)

    # Fallback sweep in case graceful shutdown didn't finish in time, the
    # state file is stale/missing, or a process got reparented away from
    # the PID we originally tracked (emulator.exe in particular tends to
    # leave a second shim process behind). Both sweeps below match on
    # command line rather than bare image name: state.json can have no
    # bridge_pid to fall back on at all (see kill_by_cmdline_match), and
    # taskkill by image name alone can't safely target "emulator.exe" or
    # "qemu-system-x86_64.exe" without also risking a real Android Studio
    # emulator instance someone has open for unrelated app development, or
    # another qemu-based tool entirely (e.g. WSL2) -- every process this
    # project launches runs out of android-sdk-portable/, which is a
    # distinctive enough path to scope the sweep to just this AVD.
    print("[stop] sweeping for any orphaned launch_bridge.py process...")
    kill_by_cmdline_match("launch_bridge.py")
    print("[stop] sweeping for any remaining emulator/qemu process for this AVD...")
    kill_by_cmdline_match("android-sdk-portable")

    # adb.exe runs as a persistent background server (any `adb` command
    # spawns it if it isn't already running) and never exits on its own
    # just because the AVD it was talking to did -- left alone, it stays
    # running indefinitely after every single Stop. `adb kill-server` is
    # the documented graceful shutdown for it, unlike taskkill against the
    # other two processes above.
    print("[stop] stopping the adb server...")
    subprocess.run(["adb", "kill-server"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)

    clear_stale_locks(avd_name)

    if STATE_PATH.is_file():
        STATE_PATH.unlink()

    print("[stop] Done. Bridge and AVD should both be fully stopped now.")


if __name__ == "__main__":
    main()
