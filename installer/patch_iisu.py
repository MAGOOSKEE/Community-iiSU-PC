"""
Applies the Community-iiSU-PC interposer patch to a copy of the iiSU APK: decompiles
it, injects the iiSU-PC bridge classes (smali_patch/), redirects its
ROM-launch startActivity call sites to LaunchBridge.launch(), rebuilds,
zipaligns, and signs the result with a freshly-generated debug keystore.

This never bundles or redistributes iiSU's own APK. It operates on a copy
supplied by whoever runs it -- the same relationship any APK-patching /
modding tool has to the app it patches.

The patch is anchored on a literal log string ("ROM launch attempt
package=") rather than hardcoded register names or line numbers, since
those are compiler-chosen and can differ between iiSU builds. It only
touches startActivity(Intent) calls inside the single method that contains
that log string, not just anywhere in the file, since MainActivity has many
unrelated startActivity calls elsewhere -- see find_enclosing_method().
"""

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from jre_env import java_subprocess_env

SCRIPT_DIR = Path(__file__).parent
SMALI_PATCH_DIR = SCRIPT_DIR / "smali_patch"
TOOLS_DIR = SCRIPT_DIR / "tools"
APKTOOL_JAR = TOOLS_DIR / "apktool.jar"

LOG_ANCHOR = "ROM launch attempt package="
STARTACTIVITY_RE = re.compile(
    r"^(\s*)invoke-virtual \{(v\d+), (v\d+)(?:, v\d+)?\}, "
    r"Landroid/content/Context;->startActivity\(Landroid/content/Intent;(?:Landroid/os/Bundle;)?\)V\s*$"
)
BRIDGE_PACKAGE_SMALI_DIR = "com/iisulauncher/pcbridge"


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({' '.join(args)}):\n{result.stdout}\n{result.stderr}")
    return result


def validate_iisu_apk(apk_path: Path) -> None:
    """Cheap pre-check that apk_path is actually iiSU, so a wrong file gets
    rejected in under a second instead of after a multi-GB SDK download and
    a full apktool decompile (the point where find_main_activity_smali()
    would otherwise be the first thing to notice). Searches the raw dex
    bytes for the same log-string anchor the real patch is anchored on --
    an ASCII string constant lands in the dex's string pool as contiguous
    UTF-8 bytes, so a plain byte search finds it without decompiling
    anything."""
    if not zipfile.is_zipfile(apk_path):
        raise RuntimeError(f"{apk_path} is not a valid APK (not a zip file).")

    anchor_bytes = LOG_ANCHOR.encode("utf-8")
    with zipfile.ZipFile(apk_path) as z:
        dex_names = [n for n in z.namelist() if re.fullmatch(r"classes\d*\.dex", n)]
        if not dex_names:
            raise RuntimeError(f"{apk_path.name} has no classes.dex -- it doesn't look like a valid Android APK.")
        found = any(anchor_bytes in z.read(name) for name in dex_names)

    if not found:
        raise RuntimeError(
            f"{apk_path.name} doesn't look like iiSU -- couldn't find its ROM-launch code in it. "
            "Double check this is the right APK (this tool only patches iiSU itself)."
        )


def decompile(apk_path: Path, out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    run(["java", "-jar", str(APKTOOL_JAR), "d", "-f", str(apk_path), "-o", str(out_dir)])


def build(decompiled_dir: Path, out_apk: Path) -> None:
    run(["java", "-jar", str(APKTOOL_JAR), "b", str(decompiled_dir), "-o", str(out_apk)])


def find_main_activity_smali(decompiled_dir: Path) -> Path:
    candidates = [p for p in decompiled_dir.rglob("MainActivity.smali") if LOG_ANCHOR in p.read_text(encoding="utf-8", errors="replace")]
    if not candidates:
        raise RuntimeError(
            "Could not find a MainActivity.smali containing the ROM-launch log anchor "
            f"({LOG_ANCHOR!r}). iiSU's code has likely changed since this patch was written "
            "and it needs to be updated by hand."
        )
    return candidates[0]


def find_enclosing_method(lines: list[str], line_index: int) -> tuple[int, int]:
    """Returns (start, end) line indices of the .method ... .end method
    block containing line_index, so the patch only touches startActivity
    calls within that one method -- not anywhere else in this very large
    file, which has many unrelated startActivity calls."""
    start = line_index
    while start >= 0 and not lines[start].lstrip().startswith(".method"):
        start -= 1
    if start < 0:
        raise RuntimeError("Could not find the enclosing .method for the ROM-launch anchor.")
    end = line_index
    while end < len(lines) and not lines[end].lstrip().startswith(".end method"):
        end += 1
    if end >= len(lines):
        raise RuntimeError("Could not find the .end method closing the ROM-launch method.")
    return start, end


def patch_main_activity(path: Path) -> int:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

    anchor_index = next((i for i, line in enumerate(lines) if LOG_ANCHOR in line), None)
    if anchor_index is None:
        raise RuntimeError(f"Anchor {LOG_ANCHOR!r} disappeared between the file-level check and patching -- this shouldn't happen.")

    method_start, method_end = find_enclosing_method(lines, anchor_index)

    patched = 0
    for i in range(method_start, method_end):
        match = STARTACTIVITY_RE.match(lines[i])
        if not match:
            continue
        indent, v_ctx, v_intent = match.groups()
        lines[i] = (
            f"{indent}invoke-static {{{v_ctx}, {v_intent}}}, "
            f"Lcom/iisulauncher/pcbridge/LaunchBridge;->launch(Landroid/content/Context;Landroid/content/Intent;)V\n"
        )
        patched += 1

    if patched == 0:
        raise RuntimeError(
            "Found the ROM-launch anchor and its enclosing method, but no startActivity(Intent) "
            "call inside it to redirect. iiSU's code has likely changed and this patch needs "
            "updating by hand."
        )

    path.write_text("".join(lines), encoding="utf-8")
    return patched



def patch_primary_home_actions_holder(decompiled_dir: Path) -> Path:
    """Expose iiSU's existing injected je6 PrimaryHomeActions instance.

    je6 already owns the synchronized WeakReference<MainActivity>. This patch
    exposes only the je6 object itself through a static field so MediaBridge can
    call je6.a() and reuse iiSU's existing activity lifecycle tracking.

    Anchored on class/method text rather than line numbers and idempotent.
    """
    candidates = []
    for path in decompiled_dir.rglob("je6.smali"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if ".class public final Lje6;" in text and ".method public final a()Lcom/iisulauncher/launcher/MainActivity;" in text:
            candidates.append(path)

    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one iiSU PrimaryHomeActions je6.smali, found {len(candidates)}. "
            "iiSU's code has likely changed and this patch needs updating by hand."
        )

    path = candidates[0]
    text = path.read_text(encoding="utf-8", errors="replace")

    static_field = ".field public static volatile c:Lje6;"
    if static_field not in text:
        field_anchor = "# instance fields"
        if field_anchor not in text:
            raise RuntimeError("Found je6.smali but could not find its instance-fields anchor.")
        text = text.replace(
            field_anchor,
            "# iiSU-PC: expose this existing injected PrimaryHomeActions holder.\n"
            f"{static_field}\n\n"
            f"{field_anchor}",
            1,
        )

    sput = "    sput-object p0, Lje6;->c:Lje6;"
    if sput not in text:
        ctor_start = text.find(".method public constructor <init>()V")
        if ctor_start < 0:
            raise RuntimeError("Found je6.smali but could not find its constructor.")

        ctor_end = text.find(".end method", ctor_start)
        if ctor_end < 0:
            raise RuntimeError("Found je6 constructor but not its .end method.")

        ctor = text[ctor_start:ctor_end]
        super_call = "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V"
        if super_call not in ctor:
            raise RuntimeError("Found je6 constructor but could not find its Object constructor call.")

        patched_ctor = ctor.replace(
            super_call,
            super_call
            + "\n\n"
            + "    # iiSU-PC: expose this same Hilt-created holder; MainActivity itself\n"
            + "    # remains referenced only by je6's existing WeakReference.\n"
            + sput,
            1,
        )
        text = text[:ctor_start] + patched_ctor + text[ctor_end:]

    path.write_text(text, encoding="utf-8")
    return path

def inject_launch_bridge(decompiled_dir: Path) -> None:
    dest = decompiled_dir / "smali" / BRIDGE_PACKAGE_SMALI_DIR
    dest.mkdir(parents=True, exist_ok=True)
    for smali_file in SMALI_PATCH_DIR.glob("*.smali"):
        shutil.copyfile(smali_file, dest / smali_file.name)


def patch_manifest_for_media_bridge(decompiled_dir: Path) -> None:
    """Register the explicit ADB-invoked MediaBridge receiver.

    No intent filter is needed: Manager addresses the component explicitly.
    exported=true is required because `adb shell am broadcast` originates
    outside iiSU's app UID. Protect the exported receiver with Android's DUMP
    permission so adb shell can invoke it while ordinary third-party apps
    cannot explicitly address the component. Re-running the patch remains
    idempotent.
    """
    manifest = decompiled_dir / "AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    receiver_name = "com.iisulauncher.pcbridge.MediaBridgeReceiver"
    receiver = (
        '        <receiver android:name="com.iisulauncher.pcbridge.MediaBridgeReceiver" '
        'android:enabled="true" android:exported="true" '
        'android:permission="android.permission.DUMP" />\n'
    )
    if receiver_name in text:
        if 'android:permission="android.permission.DUMP"' not in text:
            pattern = re.compile(
                r'[ \t]*<receiver\s+[^>]*android:name="com\.iisulauncher\.pcbridge\.MediaBridgeReceiver"[^>]*/>\n?'
            )
            text = pattern.sub(receiver, text, count=1)
            manifest.write_text(text, encoding="utf-8")
        return

    marker = "</application>"
    if marker not in text:
        raise RuntimeError("Could not find </application> in AndroidManifest.xml.")

    text = text.replace(marker, receiver + marker, 1)
    manifest.write_text(text, encoding="utf-8")


def fix_extract_native_libs(decompiled_dir: Path) -> None:
    """extractNativeLibs="false" causes INSTALL_FAILED_INVALID_APK once the
    APK is re-signed with a different key than the original -- flip it."""
    manifest = decompiled_dir / "AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace('android:extractNativeLibs="false"', 'android:extractNativeLibs="true"'), encoding="utf-8")


def zipalign(zipalign_exe: Path, unsigned_apk: Path, aligned_apk: Path) -> None:
    run([str(zipalign_exe), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)])


def sign(apksigner_exe: Path, keystore: Path, keystore_pass: str, key_alias: str, aligned_apk: Path, signed_apk: Path) -> None:
    # apksigner.bat resolves its own `java` via JAVA_HOME/PATH internally --
    # env= makes sure that resolves to a bundled JRE once one exists,
    # without ever touching the user's real system PATH/JAVA_HOME.
    run([
        str(apksigner_exe), "sign",
        "--ks", str(keystore),
        "--ks-key-alias", key_alias,
        "--ks-pass", f"pass:{keystore_pass}",
        "--key-pass", f"pass:{keystore_pass}",
        "--out", str(signed_apk),
        str(aligned_apk),
    ], env=java_subprocess_env())


def patch_apk(
    source_apk: Path,
    output_apk: Path,
    work_dir: Path,
    zipalign_exe: Path,
    apksigner_exe: Path,
    keystore: Path,
    keystore_pass: str,
    key_alias: str,
) -> None:
    decompiled_dir = work_dir / "decompiled"
    unsigned_apk = work_dir / "unsigned.apk"
    aligned_apk = work_dir / "aligned.apk"

    print(f"[patch] decompiling {source_apk.name}...")
    decompile(source_apk, decompiled_dir)

    print("[patch] locating the ROM-launch code...")
    main_activity = find_main_activity_smali(decompiled_dir)
    patched_count = patch_main_activity(main_activity)
    print(f"[patch] redirected {patched_count} startActivity call(s) to LaunchBridge in {main_activity.relative_to(decompiled_dir)}")

    print("[patch] exposing iiSU's existing PrimaryHomeActions holder...")
    primary_home_actions = patch_primary_home_actions_holder(decompiled_dir)
    print(f"[patch] patched PrimaryHomeActions holder in {primary_home_actions.relative_to(decompiled_dir)}")

    print("[patch] injecting iiSU-PC bridge classes...")
    inject_launch_bridge(decompiled_dir)
    patch_manifest_for_media_bridge(decompiled_dir)
    fix_extract_native_libs(decompiled_dir)

    print("[patch] rebuilding...")
    build(decompiled_dir, unsigned_apk)

    print("[patch] aligning and signing...")
    zipalign(zipalign_exe, unsigned_apk, aligned_apk)
    sign(apksigner_exe, keystore, keystore_pass, key_alias, aligned_apk, output_apk)

    print(f"[patch] done: {output_apk}")
