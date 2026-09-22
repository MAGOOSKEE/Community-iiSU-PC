"""
Creates a desktop shortcut that launches iiSU directly: double-clicking it
runs start_iisu_pc.py, which boots the AVD and bridge and then
auto-launches iiSU itself -- no need to open the control panel first.

Uses iiSU's own launcher icon when it can get one, extracted from the APK
you supplied for patching (installer/input/*.apk) -- never bundled or
redistributed: the extraction runs locally against your own copy each
time, and the resulting .ico is gitignored, so it's regenerated per
install rather than shipped in this project. Falls back to a generic
bundled icon if extraction isn't possible for any reason (see
extract_iisu_icon()).

Shells out to PowerShell's WScript.Shell COM object to create the actual
.lnk file, since that's the standard way to do it on Windows and needs no
extra Python package (pywin32/winshell) beyond what ships with Windows.
"""

import ctypes
import re
import shutil
import subprocess
import sys
from pathlib import Path

BRIDGE_DIR = Path(__file__).parent
PROJECT_ROOT = BRIDGE_DIR.parent
START_SCRIPT = BRIDGE_DIR / "start_iisu_pc.py"
SHORTCUT_NAME = "Community-iiSU-PC.lnk"

FALLBACK_ICON_PATH = BRIDGE_DIR / "assets" / "iisu_launch.ico"
EXTRACTED_ICON_PATH = BRIDGE_DIR / ".iisu_icon.ico"
APKTOOL_JAR = PROJECT_ROOT / "installer" / "tools" / "apktool.jar"
INPUT_DIR = PROJECT_ROOT / "installer" / "input"

ICON_DENSITY_ORDER = ["xxxhdpi", "xxhdpi", "xhdpi", "hdpi", "mdpi"]
ICON_RASTER_EXTS = [".webp", ".png"]
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _find_input_apk() -> Path | None:
    apks = sorted(INPUT_DIR.glob("*.apk"))
    return apks[0] if apks else None


def _find_manifest_icon_ref(manifest_text: str) -> tuple[str, str] | None:
    match = re.search(r'android:icon="@(mipmap|drawable)/([\w.]+)"', manifest_text)
    return (match.group(1), match.group(2)) if match else None


def _find_raster_icon(decompiled_dir: Path, icon_type: str, icon_name: str) -> Path | None:
    # Prefer the flattened legacy raster icon at the highest density
    # available over parsing/compositing an adaptive-icon XML -- real APKs
    # ship one of these alongside the adaptive icon for pre-Android-8
    # compatibility, so it's almost always there.
    for density in ICON_DENSITY_ORDER:
        for ext in ICON_RASTER_EXTS:
            candidate = decompiled_dir / "res" / f"{icon_type}-{density}" / f"{icon_name}{ext}"
            if candidate.is_file():
                return candidate
    return None


def extract_iisu_icon(apk_path: Path | None = None) -> Path | None:
    """Best-effort: pulls iiSU's own launcher icon out of the APK you
    supplied, purely so the desktop shortcut can show the real icon
    instead of a generic one. Runs apktool's resource decoder (already
    bundled for patch_iisu.py) against your own copy, reads the icon file
    it resolves, and converts it locally -- nothing here is ever committed
    to this project or sent anywhere.

    apk_path lets a caller that already knows exactly which APK it used
    (e.g. one picked via a file browser, living anywhere on disk) pass it
    straight through -- falling back to scanning installer/input/ only
    when the caller doesn't know (e.g. this module run standalone) is what
    silently produced the generic icon for anyone who picked their APK
    from somewhere else instead of dropping a copy in that folder.

    This is a cosmetic nice-to-have, not something worth failing shortcut
    creation over: needs Pillow (not a hard dependency of the rest of this
    project, since decoding the source .webp needs it) and a findable
    input APK, and returns None on any failure along the way, in which
    case create_desktop_shortcut() falls back to the generic icon."""
    try:
        from PIL import Image
    except ImportError:
        print("[shortcut] Pillow isn't installed -- using the generic icon (pip install pillow to use iiSU's own)")
        return None

    if apk_path is None:
        apk_path = _find_input_apk()
    if apk_path is None or not apk_path.is_file():
        print(f"[shortcut] no APK found under {INPUT_DIR} -- using the generic icon")
        return None

    if EXTRACTED_ICON_PATH.is_file() and EXTRACTED_ICON_PATH.stat().st_mtime > apk_path.stat().st_mtime:
        return EXTRACTED_ICON_PATH

    decompile_dir = BRIDGE_DIR / "_icon_extract_tmp"
    try:
        shutil.rmtree(decompile_dir, ignore_errors=True)
        result = subprocess.run(
            ["java", "-jar", str(APKTOOL_JAR), "d", "-s", "-f", str(apk_path), "-o", str(decompile_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[shortcut] apktool failed decoding {apk_path.name} -- using the generic icon")
            return None

        manifest_text = (decompile_dir / "AndroidManifest.xml").read_text(encoding="utf-8", errors="replace")
        icon_ref = _find_manifest_icon_ref(manifest_text)
        if icon_ref is None:
            print("[shortcut] couldn't find an application icon reference in the manifest -- using the generic icon")
            return None

        icon_file = _find_raster_icon(decompile_dir, *icon_ref)
        if icon_file is None:
            print("[shortcut] couldn't find a usable icon image at any density -- using the generic icon")
            return None

        image = Image.open(icon_file).convert("RGBA")
        # Pillow's ICO writer treats the image .save() is called on as the
        # largest available frame and silently drops any requested size
        # bigger than it -- so this must be the biggest, not the smallest.
        frames = sorted((image.resize((s, s), Image.LANCZOS) for s in ICON_SIZES), key=lambda f: f.size, reverse=True)
        frames[0].save(EXTRACTED_ICON_PATH, format="ICO", sizes=[(s, s) for s in ICON_SIZES], append_images=frames[1:])
        print(f"[shortcut] extracted iiSU's own icon from {apk_path.name}")
        return EXTRACTED_ICON_PATH
    except Exception as e:
        print(f"[shortcut] icon extraction failed ({e}) -- using the generic icon")
        return None
    finally:
        shutil.rmtree(decompile_dir, ignore_errors=True)


def desktop_dir() -> Path:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def _refresh_shell_icon_cache() -> None:
    """Windows caches rendered icon bitmaps keyed roughly by path, and
    doesn't reliably notice when a .ico file's own content changes at the
    same path (e.g. re-running setup against a different/updated APK) --
    Explorer can keep showing the old icon indefinitely otherwise. This is
    the standard, documented way to tell it to flush and re-render icon
    associations, short of restarting explorer.exe entirely."""
    SHCNE_ASSOCCHANGED = 0x08000000
    SHCNF_IDLIST = 0x0000
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


def create_desktop_shortcut(apk_path: Path | None = None) -> Path:
    extracted = extract_iisu_icon(apk_path)
    icon_path = extracted or FALLBACK_ICON_PATH
    if extracted is None:
        # extract_iisu_icon() already printed exactly why (missing Pillow,
        # apktool failure, etc.) -- this makes the *consequence* visible
        # too, since that diagnostic line is easy to miss buried in a long
        # setup log, and the shortcut otherwise looks identical either way.
        print("[shortcut] using the generic fallback icon, not iiSU's own -- see the line above for why")
    shortcut_path = desktop_dir() / SHORTCUT_NAME
    script = (
        "$shell = New-Object -ComObject WScript.Shell\n"
        f"$shortcut = $shell.CreateShortcut('{shortcut_path}')\n"
        f"$shortcut.TargetPath = '{sys.executable}'\n"
        f"$shortcut.Arguments = '\"{START_SCRIPT}\"'\n"
        f"$shortcut.WorkingDirectory = '{BRIDGE_DIR}'\n"
        f"$shortcut.IconLocation = '{icon_path}'\n"
        "$shortcut.Description = 'Launch Community-iiSU-PC'\n"
        "$shortcut.Save()\n"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create shortcut:\n{result.stdout}\n{result.stderr}")
    _refresh_shell_icon_cache()
    return shortcut_path


if __name__ == "__main__":
    path = create_desktop_shortcut()
    print(f"Created shortcut: {path}")
