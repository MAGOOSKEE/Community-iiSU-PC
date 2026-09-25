"""Non-UI logic behind the Backup & Restore page, ports manager.py's
_backup_restore_candidates/_create_manager_backup/_restore_manager_backup/
_backup_safe_member. Raises BackupServiceError instead of showing a
messagebox; the caller decides how to present that.

manager_debug.log entries the original wrote around backup/restore are not
reproduced here, that whole debug-log tee is Tk-Manager-specific
infrastructure the Qt app doesn't have yet (see Diagnostics page, which
already reports "Not found yet" for it)."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BRIDGE_DIR.parent

_RESTORE_MAP = {
    "bridge/windows_apps.json": BRIDGE_DIR / "windows_apps.json",
    "bridge/config.json": BRIDGE_DIR / "config.json",
    "config.json": PROJECT_ROOT / "config.json",
    "windows_apps.json": PROJECT_ROOT / "windows_apps.json",
    "manager_config.json": PROJECT_ROOT / "manager_config.json",
    "settings.json": PROJECT_ROOT / "settings.json",
}


class BackupServiceError(Exception):
    pass


def default_backup_filename() -> str:
    return "iisu-pc-backup-" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".zip"


def backup_candidates() -> list[tuple[Path, str]]:
    """Safe, user-created/configuration files worth backing up."""
    candidates: list[tuple[Path, str]] = []

    def add(path: Path, archive_name: str) -> None:
        try:
            if path.is_file() and not any(p.resolve() == path.resolve() for p, _ in candidates):
                candidates.append((path, archive_name))
        except Exception:
            pass

    add(BRIDGE_DIR / "windows_apps.json", "bridge/windows_apps.json")
    add(BRIDGE_DIR / "config.json", "bridge/config.json")
    add(PROJECT_ROOT / "config.json", "config.json")
    for name in ("windows_apps.json", "manager_config.json", "settings.json"):
        add(PROJECT_ROOT / name, name)

    return candidates


def create_backup(destination: Path, files: list[tuple[Path, str]]) -> None:
    manifest_lines = [
        "iiSU-PC Manager Backup",
        "Created: " + datetime.now().isoformat(timespec="seconds"),
        "",
        "Files:",
    ]
    try:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path, archive_name in files:
                zf.write(path, archive_name)
                manifest_lines.append(f"- {archive_name}")
            zf.writestr("backup_manifest.txt", "\n".join(manifest_lines) + "\n")
    except Exception as exc:
        raise BackupServiceError(f"Backup failed:\n{exc}") from exc


def backup_safe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return False
    return normalized in _RESTORE_MAP


def inspect_backup(source: Path) -> dict[str, bytes]:
    """Opens the archive, validates it, and returns {archive_name: bytes}
    for every safe/known member, callers should confirm with the user
    (showing the returned names) before calling apply_restore()."""
    try:
        with zipfile.ZipFile(source, "r") as zf:
            members = [n.replace("\\", "/") for n in zf.namelist()]
            selected = [n for n in members if backup_safe_member(n)]
            if not selected:
                raise BackupServiceError("This archive does not contain supported iiSU-PC backup files.")

            payloads: dict[str, bytes] = {}
            for name in selected:
                data = zf.read(name)
                if name.lower().endswith(".json"):
                    try:
                        json.loads(data.decode("utf-8-sig"))
                    except Exception as exc:
                        raise BackupServiceError(f"'{name}' in the archive is not valid JSON:\n{exc}") from exc
                payloads[name] = data
            return payloads
    except zipfile.BadZipFile as exc:
        raise BackupServiceError("That file is not a valid ZIP backup.") from exc


def apply_restore(payloads: dict[str, bytes]) -> tuple[int, int, Path]:
    """Writes each payload to its real location, first copying whatever it
    would overwrite into a timestamped safety folder. Returns (restored
    count, safety-copy count, safety folder path)."""
    safety_dir = PROJECT_ROOT / "restore_safety" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safety_count = 0
    for name in payloads:
        target = _RESTORE_MAP[name]
        if target.is_file():
            safety_target = safety_dir / Path(name)
            safety_target.parent.mkdir(parents=True, exist_ok=True)
            safety_target.write_bytes(target.read_bytes())
            safety_count += 1

    restored = 0
    for name, data in payloads.items():
        target = _RESTORE_MAP[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.with_name(target.name + ".restore_tmp")
        temp_target.write_bytes(data)
        temp_target.replace(target)
        restored += 1

    return restored, safety_count, safety_dir
