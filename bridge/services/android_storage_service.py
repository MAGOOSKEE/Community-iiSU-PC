"""Non-UI logic behind the Android Storage page, ADB-backed file
browsing/transfer against the Android VM's shared storage. Ports
manager.py's _adb_*/_android_*/_android_storage_* helper methods as plain
functions, extracted ahead of porting that page to Qt.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BRIDGE_DIR.parent

MEDIABRIDGE_INBOX = "/storage/emulated/0/Android/media/com.iisulauncher/iiSULauncher/mediabridge/inbox"


class AndroidStorageServiceError(Exception):
    pass


def _adb_path() -> str:
    bundled_adb = BRIDGE_DIR / "android-sdk-portable" / "sdk" / "platform-tools" / "adb.exe"
    return str(bundled_adb) if bundled_adb.is_file() else "adb"


def adb_command(*args: str, timeout: int = 30):
    flags = 0x08000000 if os.name == "nt" else 0
    return subprocess.run(
        [_adb_path(), *args],
        cwd=str(PROJECT_ROOT),
        timeout=timeout,
        creationflags=flags,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def android_remote_quote(value: str) -> str:
    """Quote one value for Android's /system/bin/sh."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def adb_shell_direct(command: str, timeout: int = 30):
    return adb_command("shell", command, timeout=timeout)


def android_join(base: str, name: str) -> str:
    if base == "/":
        return "/" + name
    return base.rstrip("/") + "/" + name


def android_parent(path: str) -> str:
    path = path.rstrip("/")
    if not path or path == "/":
        return "/"
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def adb_device_ready() -> tuple[bool, str]:
    try:
        result = adb_command("get-state", timeout=5)
    except FileNotFoundError:
        return False, "ADB was not found in PATH."
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"ADB error: {exc}"
    if result.returncode == 0 and result.stdout.strip() == "device":
        return True, "Android VM connected"
    detail = (result.stderr or result.stdout).strip()
    return False, detail or "Android VM is not connected."


def media_scan(remote_path: str) -> None:
    """Best-effort: notify Android that an ADB-side shared-storage path
    changed. Failures here are cosmetic (a stale gallery/media index), so
    callers generally shouldn't fail an otherwise-successful transfer over
    this alone, but it does raise, so a caller that cares can catch it."""
    remote_path = str(remote_path).replace("\\", "/")
    uri = "file://" + remote_path
    result = adb_command("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", uri, timeout=15)
    if result.returncode != 0:
        raise AndroidStorageServiceError(result.stderr.strip() or "Android media scan failed")


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def list_directory(path: str) -> tuple[list[tuple[str, bool, int]] | None, str]:
    """Returns (rows, status). rows is None on failure, with status
    describing why; otherwise a list of (name, is_dir, size) sorted
    folders-first then by name."""
    ready, detail = adb_device_ready()
    if not ready:
        return None, detail

    try:
        result = adb_shell_direct(f"ls -la {android_remote_quote(path)}", timeout=15)
    except Exception as exc:
        return None, str(exc)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"Can't open {path}"
        return None, detail

    rows: list[tuple[str, bool, int]] = []
    for line in result.stdout.splitlines():
        line = line.rstrip()
        if not line or line.startswith("total "):
            continue
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        perms, _links, _owner, _group, size_raw, _date, _time, name = parts
        if name in {".", ".."}:
            continue
        is_dir = perms.startswith("d")
        try:
            size = int(size_raw)
        except ValueError:
            size = 0
        rows.append((name, is_dir, size))

    rows.sort(key=lambda r: (not r[1], r[0].casefold()))
    return rows, "Android VM connected"


def upload_paths(dest: str, paths: list[str]) -> None:
    existing = [str(Path(p)) for p in paths if p and Path(p).exists()]
    for source in existing:
        result = adb_command("push", source, dest + "/", timeout=900)
        if result.returncode != 0:
            raise AndroidStorageServiceError(result.stderr.strip() or f"adb push failed for {Path(source).name}")
    if existing:
        media_scan(dest)


def download_paths(base: str, names: list[str], dest: str) -> None:
    for name in names:
        remote = android_join(base, name)
        result = adb_command("pull", remote, dest, timeout=900)
        if result.returncode != 0:
            raise AndroidStorageServiceError(result.stderr.strip() or f"adb pull failed for {name}")


def read_text_file(remote: str, max_bytes: int = 2 * 1024 * 1024) -> str:
    result = adb_shell_direct(f"cat {android_remote_quote(remote)}", timeout=30)
    if result.returncode != 0:
        raise AndroidStorageServiceError(result.stderr.strip() or f"Couldn't read {remote}.")
    content = result.stdout
    if "\x00" in content:
        raise AndroidStorageServiceError("This file appears to be binary and can't be edited as text.")
    if len(content.encode("utf-8", errors="replace")) > max_bytes:
        raise AndroidStorageServiceError("Text editing is limited to files up to 2 MB.")
    return content


def write_text_file(remote: str, content: str, suffix: str = "") -> None:
    import tempfile

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            temp_path = tmp.name
        result = adb_command("push", temp_path, remote, timeout=300)
        if result.returncode != 0:
            raise AndroidStorageServiceError(result.stderr.strip() or "adb push failed")
        media_scan(android_parent(remote))
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def make_directory(remote: str) -> None:
    result = adb_shell_direct(f"mkdir {android_remote_quote(remote)}", timeout=30)
    if result.returncode != 0:
        raise AndroidStorageServiceError(result.stderr.strip() or "mkdir failed")
    media_scan(remote)


def rename(old_remote: str, new_remote: str) -> None:
    result = adb_shell_direct(f"mv {android_remote_quote(old_remote)} {android_remote_quote(new_remote)}", timeout=30)
    if result.returncode != 0:
        raise AndroidStorageServiceError(result.stderr.strip() or "rename failed")
    media_scan(android_parent(new_remote))


def delete(remote: str, is_dir: bool) -> None:
    flag = "-rf" if is_dir else "-f"
    result = adb_shell_direct(f"rm {flag} {android_remote_quote(remote)}", timeout=60 if is_dir else 30)
    if result.returncode != 0:
        raise AndroidStorageServiceError(result.stderr.strip() or f"delete failed for {remote}")
