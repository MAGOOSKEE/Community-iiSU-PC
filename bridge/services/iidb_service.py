"""Non-UI logic behind the iiDB Browser -- search/browse iiDB's public
media API, build a cart install plan, and push originals into iiSU
through MediaBridge. Extracted ahead of porting that sub-window to Qt.

iiDB installs are Windows-Apps-only by design (see _windows_target):
Install All requires an exact Windows game-name match against an existing
.pcgame placeholder, so it never risks writing media to the wrong console
ROM entry. This is a real, if narrow, existing limitation -- not something
this port introduces.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from services.media_library_service import (
    IIDB_LIBRARY_DIR,
    media_remote_filename,
    mediabridge_install_file,
    mediabridge_rescan_library,
    register_installed_media,
)
from services.windows_apps_service import WINDOWS_STUBS_DIR

BRIDGE_DIR = Path(__file__).resolve().parent.parent
IIDB_DIR = BRIDGE_DIR / "iidb"
IIDB_API_BASE = "https://iidb.iisu.network/api/v1"
IIDB_THUMB_CACHE_DIR = IIDB_DIR / "cache" / "thumbnails"
IIDB_AUDIO_CACHE_DIR = IIDB_DIR / "cache" / "audio"

_TYPE_LABELS = {
    "iisu_boxart": "iiSU Box Arts",
    "boxart": "Box Arts",
    "icon": "Icons",
    "logo": "Logos",
    "banner": "Banners",
    "hero": "Heroes",
    "screenshot": "Screenshots",
    "soundbite": "Soundbites",
}

# Maps an iiDB category to the iiSU MediaBridge logical slot. bool=True
# means the category supports numbered slots. Windows box art is mapped to
# iiSU's icon slot deliberately -- verified against the current iiSU
# Windows platform implementation, not a guess.
_INSTALL_MAPPING = {
    "hero": ("hero", True),
    "screenshot": ("screenshot", True),
    "banner": ("screenshot", True),
    "logo": ("title", False),
    "icon": ("home_icon", False),
    "iisu_boxart": ("icon", False),
    "boxart": ("icon", False),
    "soundbite": ("soundbite", False),
}

CATEGORY_ORDER = ["iisu_boxart", "boxart", "icon", "logo", "banner", "hero", "screenshot", "soundbite"]


class IidbServiceError(Exception):
    pass


def type_label(asset_type: str) -> str:
    return _TYPE_LABELS.get(str(asset_type).lower(), str(asset_type).replace("_", " ").title())


def install_mapping(asset_type: str) -> tuple[str, bool]:
    if asset_type not in _INSTALL_MAPPING:
        raise IidbServiceError(f"Unsupported iiDB asset type: {asset_type}")
    return _INSTALL_MAPPING[asset_type]


def _json_get(path: str, params: dict | None = None, timeout: int = 15):
    """Read one iiDB JSON endpoint. iiDB is currently an unauthenticated
    public API, but it is not treated as a stable contract -- callers
    validate fields defensively."""
    url = IIDB_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "iiSU-PC Manager", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def _find_dicts(value):
    """Yield dictionaries nested in a JSON response, preserving API flexibility."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _find_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _find_dicts(child)


def search_games(query: str) -> list[dict]:
    data = _json_get("/search/suggestions", {"q": query, "mode": "default", "parent_limit": 14, "platform_limit": 4})
    found = []
    seen = set()
    for d in _find_dicts(data):
        kind = str(d.get("kind", "")).lower()
        pid = d.get("parent_id", d.get("id"))
        name = d.get("name") or d.get("title")
        if pid is None or not name:
            continue
        if kind and "parent" not in kind and "game" not in kind:
            continue
        key = str(pid)
        if key in seen:
            continue
        seen.add(key)
        found.append({"id": pid, "name": str(name), "subtitle": str(d.get("subtitle") or d.get("platform_name") or ""), "raw": d})
    return found


def load_parent_assets(parent_id) -> tuple[dict, list[dict]]:
    """Returns (landing, assets) for one iiDB parent (game)."""
    landing = _json_get(f"/parents/{parent_id}/landing")
    assets: list[dict] = []
    skip = 0
    limit = 50
    seen = set()
    while True:
        page = _json_get("/assets/browse/enriched", {"skip": skip, "limit": limit, "parent_id": parent_id}, timeout=20)
        page_assets = []
        for d in _find_dicts(page):
            if d.get("type") and (d.get("raw_url") or d.get("preview_url") or d.get("library_preview_url")):
                marker = (str(d.get("id", d.get("asset_id"))), str(d.get("filename")))
                if marker not in seen:
                    seen.add(marker)
                    page_assets.append(d)
        assets.extend(page_assets)
        if len(page_assets) < limit or len(assets) >= 1000:
            break
        skip += limit
    return landing, assets


def total_asset_count(landing: dict, fallback: int) -> int:
    for d in _find_dicts(landing):
        if isinstance(d.get("asset_count"), (int, float)):
            return int(d["asset_count"])
    return fallback


def category_counts(assets: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in assets:
        t = str(a.get("type", "")).lower()
        counts[t] = counts.get(t, 0) + 1
    return counts


def fetch_thumbnail(url: str) -> Path:
    """Downloads (or reuses a cached copy of) an asset's preview image.
    Raises on any failure; callers decode the returned path themselves
    (this has no Qt/PIL dependency)."""
    IIDB_THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(str(url).split("?", 1)[0]).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".img"
    cache = IIDB_THUMB_CACHE_DIR / (hashlib.sha256(str(url).encode()).hexdigest()[:24] + suffix)
    if not cache.is_file():
        req = urllib.request.Request(str(url), headers={"User-Agent": "iiSU-PC Manager"})
        with urllib.request.urlopen(req, timeout=15) as response:
            cache.write_bytes(response.read())
    return cache


def fetch_soundbite_preview(url: str) -> Path:
    """Downloads (or reuses a cached copy of) a soundbite preview for MCI
    playback -- unlike Media Library's saved soundbites (always PCM WAV),
    iiDB previews can be mp3/wav/etc., so this is played back via Windows'
    MCI (handles compressed formats natively) rather than WinmmPlayer."""
    IIDB_AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(str(url).split("?", 1)[0]).suffix.lower()
    if suffix not in {".mp3", ".wav", ".wma", ".m4a", ".aac", ".ogg"}:
        suffix = ".mp3"
    cache = IIDB_AUDIO_CACHE_DIR / (hashlib.sha256(str(url).encode()).hexdigest()[:24] + suffix)
    if not cache.is_file():
        req = urllib.request.Request(str(url), headers={"User-Agent": "iiSU-PC Manager"})
        with urllib.request.urlopen(req, timeout=20) as response:
            cache.write_bytes(response.read())
    return cache


def windows_target(game_name: str) -> dict:
    """Resolve an iiDB game to an existing Windows .pcgame placeholder."""
    wanted = game_name.strip().casefold()
    matches = [p for p in WINDOWS_STUBS_DIR.glob("*.pcgame") if p.stem.casefold() == wanted]
    if not matches:
        raise IidbServiceError(
            f"No matching Windows game was found for {game_name!r}.\n\n"
            f"Expected an existing placeholder named {game_name}.pcgame in:\n{WINDOWS_STUBS_DIR}\n\n"
            "Install All currently requires an exact Windows game-name match so it cannot write media to the wrong iiSU entry."
        )
    if len(matches) > 1:
        raise IidbServiceError(f"More than one matching .pcgame exists for {game_name!r}.")
    display_name = matches[0].stem
    document = f"primary:Roms/windows/{matches[0].name}"
    rom_id = "content://com.android.externalstorage.documents/tree/primary%3ARoms/document/" + urllib.parse.quote(document, safe="")
    asset_dir = "/storage/emulated/0/Android/media/com.iisulauncher/iiSULauncher/assets/media/roms/consoles/windows/" + display_name
    return {"tab_id": "windows", "rom_id": rom_id, "display_name": display_name, "asset_dir": asset_dir}


def asset_extension(asset: dict, url: str) -> str:
    filename = str(asset.get("filename") or "")
    suffix = Path(filename).suffix.lower().lstrip(".")
    if not suffix:
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower().lstrip(".")
    if not suffix:
        mime = str(asset.get("mime_type") or "").lower()
        suffix = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/ogg": "ogg",
        }.get(mime, "bin")
    return re.sub(r"[^a-z0-9]+", "", suffix) or "bin"


def download_original(item: dict, target: dict, logical_type: str) -> Path:
    asset = item["asset"]
    url = asset.get("raw_url")
    if not url:
        raise IidbServiceError("iiDB did not provide a raw/original URL for this asset.")
    extension = asset_extension(asset, str(url))
    aid = str(asset.get("id", asset.get("asset_id", "unknown")))
    safe_game = re.sub(r'[<>:"/\\|?*]+', "_", target["display_name"]).strip(" .") or "game"
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", aid) or "asset"
    durable = IIDB_LIBRARY_DIR / "windows" / safe_game / logical_type / f"{safe_id}.{extension}"
    durable.parent.mkdir(parents=True, exist_ok=True)
    temp = durable.with_suffix(durable.suffix + ".download")
    req = urllib.request.Request(str(url), headers={"User-Agent": "iiSU-PC Manager", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response, temp.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        if not temp.is_file() or temp.stat().st_size <= 0:
            raise IidbServiceError("iiDB returned an empty original file.")
        os.replace(temp, durable)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
    return durable


def cart_key(parent_id, asset: dict) -> str:
    return f"{parent_id}|{asset.get('id', asset.get('asset_id', asset.get('filename', '?')))}"


def build_install_plan(cart: dict) -> list[dict]:
    """Validate cart targets and assign deterministic iiSU slots."""
    if not cart:
        raise IidbServiceError("The iiDB cart is empty.")
    plan = []
    numbered: dict[tuple, int] = {}
    singles: set[tuple] = set()
    target_cache: dict[str, dict] = {}
    for key, item in cart.items():
        game_name = item["game_name"]
        target = target_cache.get(game_name)
        if target is None:
            target = windows_target(game_name)
            target_cache[game_name] = target
        raw_type = str(item["asset"].get("type", "")).lower()
        logical, is_numbered = install_mapping(raw_type)
        group = (target["rom_id"], logical)
        if is_numbered:
            slot = numbered.get(group, 0) + 1
            numbered[group] = slot
        else:
            if group in singles:
                raise IidbServiceError(
                    f"The cart contains more than one asset for the single iiSU slot {logical!r} "
                    f"on {target['display_name']}. Remove one before installing."
                )
            singles.add(group)
            slot = 1
        plan.append({"key": key, "item": item, "target": target, "logical_type": logical, "slot": slot})
    return plan


def install_plan(plan: list[dict]) -> tuple[list[tuple[dict, dict]], list[tuple[dict, str]], bool | None, str]:
    """Downloads + installs every entry in plan through MediaBridge, then
    triggers one Full Library Rescan if anything installed. Returns
    (installed, failures, rescan_ok, rescan_detail). installed is a list
    of (plan_entry, registry_record); failures is (plan_entry, message)."""
    installed = []
    failures = []
    for p in plan:
        item = p["item"]
        asset = item["asset"]
        target = p["target"]
        aid = asset.get("id", asset.get("asset_id"))
        try:
            durable = download_original(item, target, p["logical_type"])
            ext = durable.suffix.lower().lstrip(".")
            install_asset = {"asset_type": p["logical_type"], "slot": p["slot"], "extension": ext}
            ok, output = mediabridge_install_file(target, install_asset, durable)
            if not ok:
                match = re.search(r"IISUPC_MEDIABRIDGE_ERROR_V1:([A-Z0-9_]+)", output or "")
                raise IidbServiceError("MediaBridge: " + match.group(1) if match else (output or "MediaBridge install failed"))
            record = register_installed_media(
                tab_id=target["tab_id"],
                rom_id=target["rom_id"],
                display_name=target["display_name"],
                asset_dir=target["asset_dir"],
                asset_type=p["logical_type"],
                slot=p["slot"],
                source_file=durable,
                iidb_asset_id=aid,
                iidb_parent_id=item.get("parent_id"),
                remote_filename=media_remote_filename(p["logical_type"], p["slot"], ext),
            )
            installed.append((p, record))
        except Exception as exc:
            failures.append((p, str(exc)))

    rescan_ok = None
    rescan_detail = ""
    if installed:
        try:
            rescan_ok, rescan_detail = mediabridge_rescan_library()
        except Exception as exc:
            rescan_ok = False
            rescan_detail = str(exc)
    return installed, failures, rescan_ok, rescan_detail
