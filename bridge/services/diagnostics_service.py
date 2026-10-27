"""Non-UI logic behind the Diagnostics page, kept apart so it's
callable/testable without a Qt event loop.

Each diagnostic check is a plain (status, check, details) tuple with
status one of "OK"/"WARNING"/"ERROR", same vocabulary the original used."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from services import windows_apps_service
from shared import app_version, vm_storage

BRIDGE_DIR = Path(__file__).resolve().parent.parent
MANAGER_LOG_PATH = BRIDGE_DIR / "manager_debug.log"


def _adb_path() -> str:
    adb_name = "adb.exe" if os.name == "nt" else "adb"
    bundled_adb = BRIDGE_DIR / "android-sdk-portable" / "sdk" / "platform-tools" / adb_name
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


def vm_storage_check(df_output: str, configured_gb: float | None = None) -> tuple[str, str, str]:
    """The Diagnostics row for the Android VM's internal storage:
    (status, check name, details). A full data partition is what makes iiSU
    crash with "database or disk is full", so a nearly full one is an error,
    and the details say what to do about it."""
    check = "Android VM storage"
    usage = vm_storage.parse_df(df_output)
    if usage is None:
        return "WARNING", check, "Couldn't read the VM's storage usage."
    summary = vm_storage.describe(usage)
    level = vm_storage.usage_level(usage)
    small = configured_gb is not None and configured_gb < vm_storage.DEFAULT_DATA_PARTITION_GB
    advice = (
        f" This VM's data partition is {configured_gb:g} GB; new installs get {vm_storage.DEFAULT_DATA_PARTITION_GB} GB. "
        "See \"If the VM runs out of internal storage\" in the README to rebuild it bigger."
        if small else ""
    )
    if level == "critical":
        return "ERROR", check, f"{summary}. iiSU crashes with \"database or disk is full\" at this point." + advice
    if level == "low":
        return "WARNING", check, f"{summary}. Running low." + advice
    return "OK", check, summary


def run_diagnostics(config_data: dict) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []

    def add(status: str, check: str, details: str) -> None:
        results.append((status, check, details))

    add("OK", "Version", app_version.banner("diagnostics"))

    config_path = BRIDGE_DIR / "config.json"
    apps_path = BRIDGE_DIR / "windows_apps.json"
    adb_path = BRIDGE_DIR / "android-sdk-portable" / "sdk" / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")

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
                    df = _adb_command("shell", "df -h /data", timeout=10)
                    configured_gb = None
                    avd_config = BRIDGE_DIR / "android-sdk-portable" / "avd-home" / f"{config.get('avd_name', 'iisuwin')}.avd" / "config.ini"
                    try:
                        configured_gb = vm_storage.parse_configured_gb(avd_config.read_text(encoding="utf-8"))
                    except OSError:
                        pass
                    add(*vm_storage_check(df.stdout or "", configured_gb))
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

    steam_libraries = windows_apps_service.steam_library_paths()
    found_steam = steam_libraries[0] if steam_libraries else None
    if found_steam:
        vdf = found_steam / "steamapps" / "libraryfolders.vdf"
        add("OK", "Steam installation", str(found_steam))
        add("OK" if vdf.is_file() else "WARNING", "Steam library config", str(vdf) if vdf.is_file() else f"Not found: {vdf}")
    else:
        add("WARNING", "Steam installation", "Default Steam installation was not detected")

    if os.name != "nt":
        if shutil.which("xdotool"):
            add("OK", "xdotool", "Found, window management/fullscreen is fully available")
        else:
            add("WARNING", "xdotool", "Not found, fullscreen/window management will be limited (install it from your package manager)")
        if shutil.which("flatpak"):
            add("OK", "flatpak", "Found, the Manager's emulator downloader is available")
        else:
            add("WARNING", "flatpak", "Not found, the Manager's emulator downloader needs it (or install PC emulators yourself)")
        if shutil.which("qdbus") or shutil.which("qdbus6"):
            add("OK", "qdbus (KWin scripting)", "Found, KWin-based fullscreen is available on KDE Plasma")
        else:
            add("WARNING", "qdbus (KWin scripting)", "Not found, xdotool alone still covers most fullscreen/window handling off KDE Plasma")

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


@dataclass
class UpdateCheckResult:
    message: str
    update_available: bool
    # What's in the update: new commit subjects for a git checkout, the
    # release notes for a packaged install. Empty when there's nothing to say.
    notes: str = ""


def trim_release_notes(body: str | None, limit: int = 1500) -> str:
    """Release notes as plain text for a small read-only box: normalized
    newlines, no leading/trailing blank space, and cut at a line boundary
    (with a marker) when over limit."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    if "\n" not in head:
        return head + "..."
    return head.rsplit("\n", 1)[0].rstrip() + "\n..."


def check_for_updates_detailed() -> UpdateCheckResult:
    """Read-only: never downloads or installs anything, just reports
    whether a newer commit/release is available, and whether Updates
    page's Install Update action would actually do anything right now."""
    import updater

    if updater.is_git_checkout():
        branch = updater.current_branch()
        if branch is None:
            return UpdateCheckResult("Can't compare updates: this Git checkout is on a detached HEAD.", False)
        fetch = updater._run_git(["fetch", "origin", branch])
        if fetch is None or fetch.returncode != 0:
            reason = fetch.stderr.strip()[:200] if fetch else "git not found or fetch timed out"
            return UpdateCheckResult(f"Couldn't check GitHub: {reason}", False)
        local = updater._run_git(["rev-parse", "HEAD"])
        remote = updater._run_git(["rev-parse", f"origin/{branch}"])
        local_sha = local.stdout.strip() if local and local.returncode == 0 else None
        remote_sha = remote.stdout.strip() if remote and remote.returncode == 0 else None
        if not local_sha or not remote_sha:
            return UpdateCheckResult("Couldn't compare local and remote commits.", False)
        if local_sha == remote_sha:
            return UpdateCheckResult(f"Up to date on {branch}. Nothing was downloaded or installed.", False)
        count = updater._run_git(["rev-list", "--count", f"HEAD..origin/{branch}"])
        behind = count.stdout.strip() if count and count.returncode == 0 else "one or more"
        log = updater._run_git(["log", "--format=- %s", "-n", "15", f"HEAD..origin/{branch}"])
        notes = log.stdout.strip() if log and log.returncode == 0 else ""
        return UpdateCheckResult(f"Update available: {behind} new commit(s) on {branch}.", True, notes)

    current = updater.get_installed_version()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{updater.GITHUB_REPO}/releases",
        headers={"User-Agent": "Community-iiSU-PC", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=updater.HTTP_TIMEOUT) as resp:
        releases = json.loads(resp.read())
    if not releases:
        return UpdateCheckResult("No Community-iiSU-PC releases are published yet.", False)
    latest = releases[0]["tag_name"]
    if current == latest:
        return UpdateCheckResult(f"Up to date ({current}). Nothing was downloaded or installed.", False)
    if current is None:
        return UpdateCheckResult(
            f"Latest release: {latest}. This install has no VERSION file for comparison. Nothing was downloaded or installed.",
            False,
        )
    return UpdateCheckResult(f"Update available: {latest} (installed: {current}).", True, trim_release_notes(releases[0].get("body")))
