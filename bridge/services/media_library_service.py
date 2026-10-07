"""Non-UI logic behind the Media Library page: the durable saved-artwork
registry and MediaBridge install/verify/rescan calls.

ADB plumbing (adb_command/adb_shell_direct/android_remote_quote) is
reused from android_storage_service rather than duplicated a third time.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from services.android_storage_service import adb_command, adb_shell_direct, android_remote_quote

BRIDGE_DIR = Path(__file__).resolve().parent.parent
# The on-disk folder is still named "iidb": renaming it would orphan every
# existing user's saved library and registry for no functional gain.
MEDIA_DIR = BRIDGE_DIR / "iidb"
MEDIA_LIBRARY_DIR = MEDIA_DIR / "library"
MEDIA_REGISTRY_PATH = MEDIA_DIR / "installed_media.json"

MEDIABRIDGE_INBOX = "/storage/emulated/0/Android/media/com.iisulauncher/iiSULauncher/mediabridge/inbox"
MEDIABRIDGE_COMPONENT = "com.iisulauncher/com.iisulauncher.pcbridge.MediaBridgeReceiver"
MEDIABRIDGE_INSTALL_ACTION = "com.iisulauncher.pcbridge.INSTALL_ROM_ASSET"
MEDIABRIDGE_PING_ACTION = "com.iisulauncher.pcbridge.PING"
MEDIABRIDGE_RESCAN_ACTION = "com.iisulauncher.pcbridge.RESCAN_LIBRARY"

_REMOTE_FILENAME_BASE = {
    "hero": "hero_{slot}",
    "screenshot": "slide_{slot}",
    "title": "title",
    "icon": "icon",
    "home_icon": "home_icon",
    "soundbite": "music",
    "portrait": "portrait",
}


class MediaLibraryServiceError(Exception):
    pass


# == Registry ==


def media_registry_empty() -> dict:
    return {"version": 1, "games": {}}


def load_media_registry() -> dict:
    if not MEDIA_REGISTRY_PATH.is_file():
        return media_registry_empty()
    try:
        data = json.loads(MEDIA_REGISTRY_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("games"), dict):
            raise ValueError("Unsupported or invalid installed media registry")
        return data
    except Exception as exc:
        raise MediaLibraryServiceError(f"Couldn't read installed media registry:\n{MEDIA_REGISTRY_PATH}\n\n{exc}") from exc


def save_media_registry(registry: dict) -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(registry, indent=2, ensure_ascii=False) + "\n"
    temp = MEDIA_REGISTRY_PATH.with_suffix(".json.tmp")
    temp.write_text(payload, encoding="utf-8")
    os.replace(temp, MEDIA_REGISTRY_PATH)


def media_records(registry: dict):
    """Yields (key, game, asset) for every saved asset in the registry."""
    for key, game in registry.get("games", {}).items():
        if not isinstance(game, dict):
            continue
        for asset in game.get("assets", []):
            if isinstance(asset, dict):
                yield key, game, asset


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def media_game_key(tab_id: str, rom_id: str) -> str:
    return f"{tab_id}|{rom_id}"


def media_remote_filename(asset_type: str, slot: int, extension: str) -> str:
    base = _REMOTE_FILENAME_BASE.get(asset_type)
    if not base:
        raise ValueError(f"Unsupported media asset type: {asset_type}")
    return f"{base.format(slot=slot)}.{extension}"


def media_local_file(asset: dict) -> Path:
    # Registry v1 paths are relative to the library folder. Tolerate the early
    # bootstrap form that accidentally included a leading "library/".
    relative = Path(str(asset.get("file", "")))
    parts = relative.parts
    if parts and parts[0].lower() == "library":
        relative = Path(*parts[1:])
    return MEDIA_LIBRARY_DIR / relative


def media_asset_remote_path(game: dict, asset: dict) -> str:
    remote_filename = asset.get("remote_filename")
    if not remote_filename:
        extension = str(asset.get("extension") or Path(str(asset.get("file", ""))).suffix.lstrip(".") or "bin").lower()
        remote_filename = media_remote_filename(str(asset.get("asset_type", "")), int(asset.get("slot", 1)), extension)
    return str(game["asset_dir"]).rstrip("/") + "/" + str(remote_filename)


def media_check_asset(game: dict, asset: dict) -> tuple[str, str]:
    local_file = media_local_file(asset)
    if not local_file.is_file():
        return "LOCAL_MISSING", f"Local copy missing: {local_file}"
    local_hash = sha256_file(local_file)
    expected = str(asset.get("sha256", "")).lower()
    if expected and local_hash.lower() != expected:
        return "LOCAL_CHANGED", "Local library file no longer matches its registry hash"
    remote = media_asset_remote_path(game, asset)
    result = adb_shell_direct(f"sha256sum {android_remote_quote(remote)}", timeout=20)
    if result.returncode != 0:
        return "REMOTE_MISSING", remote
    remote_hash = (result.stdout or "").strip().split(None, 1)[0].lower()
    if remote_hash == local_hash.lower():
        return "OK", remote_hash
    return "REMOTE_CHANGED", remote_hash or "Different file"


def human_size(value) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


# == MediaBridge ==


def mediabridge_ping() -> tuple[bool, str]:
    result = adb_command("shell", "am", "broadcast", "-a", MEDIABRIDGE_PING_ACTION, "-n", MEDIABRIDGE_COMPONENT, timeout=15)
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return result.returncode == 0 and "result=1" in output and "IISUPC_MEDIABRIDGE_READY_V1" in output, output


def mediabridge_rescan_library() -> tuple[bool, str]:
    """Trigger iiSU's native Full Library Rescan through MediaBridge."""
    result = adb_command("shell", "am", "broadcast", "-a", MEDIABRIDGE_RESCAN_ACTION, "-n", MEDIABRIDGE_COMPONENT, timeout=60)
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    ok = result.returncode == 0 and "result=1" in output and "IISUPC_MEDIABRIDGE_RESCAN_STARTED_V1" in output
    return ok, output


def mediabridge_install_file(game: dict, asset: dict, local_file: Path) -> tuple[bool, str]:
    extension = str(asset.get("extension") or local_file.suffix.lstrip(".")).lower()
    stage_name = f"iisupc-{uuid.uuid4().hex}.{extension}"
    stage_remote = f"{MEDIABRIDGE_INBOX}/{stage_name}"
    mkdir = adb_shell_direct(f"mkdir -p {android_remote_quote(MEDIABRIDGE_INBOX)}", timeout=15)
    if mkdir.returncode != 0:
        return False, (mkdir.stderr or mkdir.stdout or "Could not create MediaBridge inbox").strip()
    pushed = adb_command("push", str(local_file), stage_remote, timeout=300)
    if pushed.returncode != 0:
        return False, (pushed.stderr or pushed.stdout or "adb push failed").strip()
    try:
        # adb shell ultimately passes this through Android's shell. Build one
        # explicitly quoted command so values containing spaces, URI punctuation,
        # percent escapes, etc. remain one --es value.
        extras = (
            ("tab_id", game.get("tab_id")),
            ("rom_id", game.get("rom_id")),
            ("asset_dir", game.get("asset_dir")),
            ("source", stage_remote),
            ("asset_type", asset.get("asset_type")),
            ("extension", extension),
        )
        missing = [name for name, value in extras if value is None or str(value) == ""]
        if missing:
            return False, "Manager registry is missing required field(s): " + ", ".join(missing)
        command = (
            f"am broadcast -a {android_remote_quote(MEDIABRIDGE_INSTALL_ACTION)} "
            f"-n {android_remote_quote(MEDIABRIDGE_COMPONENT)} "
            + " ".join(f"--es {android_remote_quote(name)} {android_remote_quote(str(value))}" for name, value in extras)
        )
        result = adb_shell_direct(command, timeout=60)
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        ok = result.returncode == 0 and "result=1" in output and "IISUPC_MEDIABRIDGE_INSTALLED_V1" in output
        return ok, output
    finally:
        adb_shell_direct(f"rm -f {android_remote_quote(stage_remote)}", timeout=15)
