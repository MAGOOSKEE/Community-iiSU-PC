"""
Builds and installs minimal "redirector" stub APKs: placeholder apps that
declare a specific package name and nothing else, so iiSU's own
installed-package check resolves a console's chosen emulator to a package
our patched LaunchBridge already knows how to intercept (see
shared/emulator_defaults.py for why, and which packages this covers by
default). The stub is never actually launched in normal operation -- our
patch redirects to the real PC emulator before Android would ever start
it, so its one Activity does nothing but immediately finish() if it ever
somehow is.

Uses the same apktool.jar already bundled for patch_iisu.py to compile
the manifest and assemble the (identical, hand-written) smali for every
stub -- only the manifest's declared package/label differ between builds,
never the code. Needs zipalign/apksigner from Android's build-tools;
unlike patch_iisu.py's one-time use of the installer's own SDK copy
(deleted after setup, see setup_wizard.py's cleanup_installer_sdk()),
stub building can happen any time from the configurator, so a copy of
build-tools is kept permanently under installer/tools/ instead (see
preserve_build_tools()) -- ~137MB, small next to the ~3.5GB that actually
does get cleaned up.
"""

import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

INSTALLER_DIR = Path(__file__).parent

sys.path.insert(0, str(INSTALLER_DIR.parent / "bridge"))
import portable_sdk  # noqa: E402,F401 -- imported for its import-time PATH fix (adb), not used directly here

TEMPLATE_DIR = INSTALLER_DIR / "stub_apk_template"
APKTOOL_JAR = INSTALLER_DIR / "tools" / "apktool.jar"
WORK_DIR = INSTALLER_DIR / "_work" / "stub_build"

BUILD_TOOLS_VERSION = "34.0.0"
BUILD_TOOLS_DIR = INSTALLER_DIR / "tools" / "build-tools" / BUILD_TOOLS_VERSION

KEYSTORE_DIR = INSTALLER_DIR / "keystore"
KEYSTORE_PATH = KEYSTORE_DIR / "stub-apps.keystore"
KEYSTORE_META_PATH = KEYSTORE_DIR / "stub-apps-keystore.json"
KEY_ALIAS = "iisu-pc-stub"

REDIRECTOR_ACTIVITY = "com.iisupc.stub.RedirectorActivity"


def zipalign_exe() -> Path:
    return BUILD_TOOLS_DIR / "zipalign.exe"


def apksigner_bat() -> Path:
    return BUILD_TOOLS_DIR / "apksigner.bat"


def build_tools_available() -> bool:
    return zipalign_exe().is_file() and apksigner_bat().is_file()


def preserve_build_tools(source_sdk_root: Path) -> None:
    """Copies build-tools out of the installer's temporary SDK copy before
    cleanup_installer_sdk() deletes it. Safe to call even if build-tools
    are already preserved or the source is gone (e.g. re-running this
    against an install that already cleaned up) -- just a no-op then."""
    if build_tools_available():
        return
    source = source_sdk_root / "build-tools" / BUILD_TOOLS_VERSION
    if not source.is_dir():
        return
    BUILD_TOOLS_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, BUILD_TOOLS_DIR, dirs_exist_ok=True)


def ensure_keystore() -> tuple[Path, str]:
    """Separate from the keystore patch_iisu.py generates for iiSU itself:
    stub apps are unrelated packages with their own identities, but all
    stubs share this one key so a stub can be rebuilt/replaced later
    without hitting a signature mismatch against itself."""
    KEYSTORE_DIR.mkdir(parents=True, exist_ok=True)
    if KEYSTORE_PATH.is_file() and KEYSTORE_META_PATH.is_file():
        meta = json.loads(KEYSTORE_META_PATH.read_text(encoding="utf-8"))
        return KEYSTORE_PATH, meta["password"]

    password = secrets.token_hex(16)
    result = subprocess.run(
        [
            "keytool", "-genkeypair", "-v",
            "-keystore", str(KEYSTORE_PATH),
            "-alias", KEY_ALIAS,
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-storepass", password, "-keypass", password,
            "-dname", "CN=iiSU-PC Redirector, OU=iiSU-PC, O=iiSU-PC, L=Local, S=Local, C=US",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"keytool failed:\n{result.stdout}\n{result.stderr}")

    KEYSTORE_META_PATH.write_text(json.dumps({"password": password}), encoding="utf-8")
    return KEYSTORE_PATH, password


def _run(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({' '.join(args)}):\n{result.stdout}\n{result.stderr}")
    return result


def _manifest_xml(package_name: str, app_label: str) -> str:
    # Full XML-attribute escaping (&/</>/"), not just quotes -- app_label is
    # only ever one of this project's own hardcoded labels today, but a
    # future one containing e.g. "&" would otherwise produce a manifest
    # apktool can't parse.
    safe_label = xml_escape(app_label, {'"': "&quot;"})
    safe_package = xml_escape(package_name, {'"': "&quot;"})
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
        f'    package="{safe_package}">\n'
        # Deliberately no android:icon. iiSU's own "Installed Emulators"
        # picker builds its list from a plain PackageManager query
        # (ACTION_MAIN + CATEGORY_LAUNCHER, MATCH_ALL; verified by
        # decompiling iiSU's own APK) that this manifest already satisfies
        # with no icon at all -- a real icon was tried once (a mipmap
        # resource, see git history) but broke every stub install outright
        # on API 30+ system images: PackageManager rejects any APK there
        # whose resources.arsc isn't stored uncompressed and 4-byte
        # aligned, which apktool's build doesn't guarantee the moment
        # there's an actual resource to compile. A cosmetic icon isn't
        # worth trading for stubs that don't install at all.
        f'    <application android:label="{safe_label} (iiSU-PC redirector)" android:hasCode="true">\n'
        f'        <activity android:name="{REDIRECTOR_ACTIVITY}" android:exported="true">\n'
        '            <intent-filter>\n'
        '                <action android:name="android.intent.action.MAIN"/>\n'
        '                <category android:name="android.intent.category.LAUNCHER"/>\n'
        '            </intent-filter>\n'
        '        </activity>\n'
        '    </application>\n'
        '</manifest>\n'
    )


def build_stub_apk(package_name: str, app_label: str, output_apk: Path) -> None:
    """Builds one signed, installable stub APK declaring package_name and
    nothing else. The manifest is the only thing that changes between
    stubs -- the smali (a single Activity that finish()es immediately) is
    identical every time, copied from stub_apk_template/ verbatim."""
    if not build_tools_available():
        raise RuntimeError(
            f"build-tools not found under {BUILD_TOOLS_DIR} -- re-run Setup.bat once to restore them "
            "(preserve_build_tools() keeps a permanent copy going forward)."
        )

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    project_dir = WORK_DIR / "project"
    if project_dir.exists():
        shutil.rmtree(project_dir)
    shutil.copytree(TEMPLATE_DIR, project_dir)
    (project_dir / "AndroidManifest.xml").write_text(_manifest_xml(package_name, app_label), encoding="utf-8")

    unsigned_apk = WORK_DIR / "unsigned.apk"
    aligned_apk = WORK_DIR / "aligned.apk"
    _run(["java", "-jar", str(APKTOOL_JAR), "b", str(project_dir), "-o", str(unsigned_apk)])
    _run([str(zipalign_exe()), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)])

    keystore, keystore_pass = ensure_keystore()
    output_apk.parent.mkdir(parents=True, exist_ok=True)
    _run([
        str(apksigner_bat()), "sign",
        "--ks", str(keystore),
        "--ks-key-alias", KEY_ALIAS,
        "--ks-pass", f"pass:{keystore_pass}",
        "--key-pass", f"pass:{keystore_pass}",
        "--out", str(output_apk),
        str(aligned_apk),
    ])


def install_stub_apk(apk_path: Path, package_name: str, replace_existing: bool = False) -> str:
    """Returns "installed" on a normal install, "replaced" if something
    else was there and replace_existing let this overwrite it, or
    "conflict" if something else is installed under this exact package
    and replace_existing is False.

    A signature mismatch here is ambiguous -- it could be an old-style
    hand-installed stub (safe to replace), but it could just as easily be
    the console's *real* Android app the person actually installed on
    purpose (replacing that would be a genuinely surprising, unwanted
    data-loss-shaped action). Defaults to leaving it alone; callers that
    know better (the person explicitly confirming a replace from the
    configurator) can pass replace_existing=True."""
    result = subprocess.run(["adb", "install", "-r", str(apk_path)], capture_output=True, text=True)
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in result.stdout or "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in result.stderr:
        if not replace_existing:
            return "conflict"
        subprocess.run(["adb", "uninstall", package_name], capture_output=True, text=True)
        result = subprocess.run(["adb", "install", str(apk_path)], capture_output=True, text=True)
        if result.returncode != 0 or "Success" not in result.stdout:
            raise RuntimeError(f"adb install failed:\n{result.stdout}\n{result.stderr}")
        return "replaced"
    if result.returncode != 0 or "Success" not in result.stdout:
        raise RuntimeError(f"adb install failed:\n{result.stdout}\n{result.stderr}")
    return "installed"


def build_and_install(package_name: str, app_label: str, replace_existing: bool = False) -> str:
    output_apk = WORK_DIR / f"{package_name}.apk"
    build_stub_apk(package_name, app_label, output_apk)
    return install_stub_apk(output_apk, package_name, replace_existing=replace_existing)
