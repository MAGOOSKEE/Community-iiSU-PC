"""Non-UI logic behind the Diagnostics page -- ports manager.py's
_run_diagnostics/_diagnostics_check_for_updates_now, extracted so it's
callable/testable without a Tk or Qt event loop.

Each diagnostic check is a plain (status, check, details) tuple with
status one of "OK"/"WARNING"/"ERROR", same vocabulary the original used."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
MANAGER_LOG_PATH = BRIDGE_DIR / "manager_debug.log"


def _adb_path() -> str:
    bundled_adb = BRIDGE_DIR / "android-sdk-portable" / "sdk" / "platform-tools" / "adb.exe"
    return str(bundled_adb) if bundled_adb.is_file() else "adb"


def _adb_command(*args: str, timeout: int = 30):
    flags = 0x08000000 if os.name == "nt" else 0
    return subprocess.run(
        [_adb_path(), *args],
        cwd=str(BRIDGE_DIR.parent),
        timeout=timeout,
        creationflags=flags,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def run_diagnostics(config_data: dict) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []

    def add(status: str, check: str, details: str) -> None:
        results.append((status, check, details))

    config_path = BRIDGE_DIR / "config.json"
    apps_path = BRIDGE_DIR / "windows_apps.json"
    adb_path = BRIDGE_DIR / "android-sdk-portable" / "sdk" / "platform-tools" / "adb.exe"

    add("OK" if BRIDGE_DIR.is_dir() else "ERROR", "Bridge directory", str(BRIDGE_DIR) if BRIDGE_DIR.is_dir() else f"Missing: {BRIDGE_DIR}")
    add("OK" if config_path.is_file() else "ERROR", "Bridge config", str(config_path) if config_path.is_file() else "bridge/config.json is missing")
    add("OK" if apps_path.is_file() else "WARNING", "Windows Apps config", str(apps_path) if apps_path.is_file() else "windows_apps.json is missing")
    add("OK" if adb_path.is_file() else "ERROR", "Bundled ADB", str(adb_path) if adb_path.is_file() else f"Missing: {adb_path}")

    config: dict = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            add("OK", "Bridge config JSON", "Valid JSON")
        except Exception as exc:
            add("ERROR", "Bridge config JSON", f"Invalid JSON: {exc}")

    if apps_path.is_file():
        try:
            apps = json.loads(apps_path.read_text(encoding="utf-8-sig"))
            if isinstance(apps, dict):
                add("OK", "Windows Apps JSON", f"Valid JSON \u2022 {len(apps)} entr{'y' if len(apps) == 1 else 'ies'}")
            else:
                add("ERROR", "Windows Apps JSON", "Top-level JSON value is not an object")
        except Exception as exc:
            add("ERROR", "Windows Apps JSON", f"Invalid JSON: {exc}")

    bridge_port = config.get("bridge_port", 7737) if isinstance(config, dict) else 7737
    try:
        bridge_port = int(bridge_port)
        if 1 <= bridge_port <= 65535:
            add("OK", "Bridge port setting", str(bridge_port))
        else:
            add("ERROR", "Bridge port setting", f"Invalid port: {bridge_port}")
    except Exception:
        add("ERROR", "Bridge port setting", f"Invalid value: {bridge_port!r}")

    roms_dir = config.get("roms_dir") if isinstance(config, dict) else None
    if roms_dir:
        rp = Path(roms_dir)
        add("OK" if rp.is_dir() else "ERROR", "ROMs directory", str(rp) if rp.is_dir() else f"Configured path does not exist: {rp}")
        windows_roms = rp / "windows"
        add("OK" if windows_roms.is_dir() else "WARNING", "Windows ROMs folder", str(windows_roms) if windows_roms.is_dir() else f"Not found: {windows_roms}")
    else:
        add("WARNING", "ROMs directory", "roms_dir is not configured")

    if adb_path.is_file():
        try:
            state = _adb_command("get-state", timeout=10)
            state_text = (state.stdout or "").strip()
            if state.returncode == 0 and state_text == "device":
                add("OK", "Android VM / ADB", "Device is connected")
                quoted = "'" + "/storage/emulated/0".replace("'", "'\\''") + "'"
                listing = _adb_command("shell", f"ls -ld {quoted}", timeout=10)
                if listing.returncode == 0:
                    add("OK", "Android shared storage", "/storage/emulated/0 is accessible")
                else:
                    add("ERROR", "Android shared storage", (listing.stderr or listing.stdout or "Unable to access shared storage").strip())
            else:
                add("ERROR", "Android VM / ADB", state_text or (state.stderr or "ADB device is not ready").strip())
        except Exception as exc:
            add("ERROR", "Android VM / ADB", str(exc))

    try:
        port = int(bridge_port)
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            add("OK", "Launch Bridge listener", f"Listening on localhost:{port}")
    except Exception:
        add("WARNING", "Launch Bridge listener", f"Nothing accepted a connection on localhost:{bridge_port} (normal if the bridge is not running)")

    steam_roots = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam",
    ]
    found_steam = next((p for p in steam_roots if p.is_dir()), None)
    if found_steam:
        vdf = found_steam / "steamapps" / "libraryfolders.vdf"
        add("OK", "Steam installation", str(found_steam))
        add("OK" if vdf.is_file() else "WARNING", "Steam library config", str(vdf) if vdf.is_file() else f"Not found: {vdf}")
    else:
        add("WARNING", "Steam installation", "Default Steam installation was not detected")

    for label, path in (("Manager log", MANAGER_LOG_PATH), ("Bridge log", BRIDGE_DIR / "bridge_debug.log")):
        if path.is_file():
            try:
                size = path.stat().st_size
                add("OK", label, f"{path} \u2022 {size:,} bytes")
            except Exception:
                add("OK", label, str(path))
        else:
            add("WARNING", label, f"Not found yet: {path}")

    safety = BRIDGE_DIR / "restore_safety"
    if safety.is_dir():
        try:
            count = sum(1 for p in safety.iterdir() if p.is_dir())
            add("OK", "Restore safety copies", f"{count} restore safety set(s) \u2022 {safety}")
        except Exception:
            add("OK", "Restore safety copies", str(safety))
    else:
        add("OK", "Restore safety copies", "No restore safety folder yet")

    return results


def summarize(results: list[tuple[str, str, str]]) -> str:
    errors = sum(1 for status, _, _ in results if status == "ERROR")
    warnings = sum(1 for status, _, _ in results if status == "WARNING")
    oks = sum(1 for status, _, _ in results if status == "OK")
    return f"{oks} OK \u2022 {warnings} Warning{'s' if warnings != 1 else ''} \u2022 {errors} Error{'s' if errors != 1 else ''}"


def check_for_updates() -> str:
    """Read-only: never downloads or installs anything, just reports
    whether a newer commit/release is available."""
    import updater

    if updater.is_git_checkout():
        branch = updater.current_branch()
        if branch is None:
            return "Can't compare updates: this Git checkout is on a detached HEAD."
        fetch = updater._run_git(["fetch", "origin", branch])
        if fetch is None or fetch.returncode != 0:
            reason = fetch.stderr.strip()[:200] if fetch else "git not found or fetch timed out"
            return f"Couldn't check GitHub: {reason}"
        local = updater._run_git(["rev-parse", "HEAD"])
        remote = updater._run_git(["rev-parse", f"origin/{branch}"])
        local_sha = local.stdout.strip() if local and local.returncode == 0 else None
        remote_sha = remote.stdout.strip() if remote and remote.returncode == 0 else None
        if not local_sha or not remote_sha:
            return "Couldn't compare local and remote commits."
        if local_sha == remote_sha:
            return f"Up to date on {branch}. Nothing was downloaded or installed."
        count = updater._run_git(["rev-list", "--count", f"HEAD..origin/{branch}"])
        behind = count.stdout.strip() if count and count.returncode == 0 else "one or more"
        return f"Update available: {behind} new commit(s) on {branch}. Nothing was downloaded or installed."

    current = updater.VERSION_PATH.read_text(encoding="utf-8").strip() if updater.VERSION_PATH.is_file() else None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{updater.GITHUB_REPO}/releases",
        headers={"User-Agent": "Community-iiSU-PC", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=updater.HTTP_TIMEOUT) as resp:
        releases = json.loads(resp.read())
    if not releases:
        return "No Community-iiSU-PC releases are published yet."
    latest = releases[0]["tag_name"]
    if current == latest:
        return f"Up to date ({current}). Nothing was downloaded or installed."
    if current is None:
        return f"Latest release: {latest}. This install has no VERSION file for comparison. Nothing was downloaded or installed."
    return f"Update available: {latest} (installed: {current}). Nothing was downloaded or installed."
