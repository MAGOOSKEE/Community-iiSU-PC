"""
Applies the Community-iiSU-PC interposer patch to a copy of the iiSU APK: decompiles
it, injects the iiSU-PC bridge classes (smali_patch/), redirects its
ROM-launch startActivity call sites to LaunchBridge.launch(), rebuilds,
zipaligns, and signs the result with a freshly-generated debug keystore.

This never bundles or redistributes iiSU's own APK. It operates on a copy
supplied by whoever runs it, the same relationship any APK-patching /
modding tool has to the app it patches.

The patch is anchored on a literal log string ("ROM launch attempt
package=") rather than hardcoded register names or line numbers, since
those are compiler-chosen and can differ between iiSU builds. It only
touches startActivity(Intent) calls inside the single method that contains
that log string, not just anywhere in the file, since MainActivity has many
unrelated startActivity calls elsewhere, see find_enclosing_method().

As of iiSU Alpha 7.5-prerelease1, LOG_ANCHOR no longer exists at all, that
build moved the actual dispatch out of MainActivity entirely into its own
single-purpose "GameLaunchCoordinator" class (confirmed live via adb
logcat while launching a real game: MainActivity now only logs "Tracking
launched package display=..." *after* the real launch already happened
elsewhere). That class's own obfuscated name changes every rebuild (seen
as "rw2" in 7.5-prerelease1) same as je6/PrimaryHomeActions below, so
find_game_launch_coordinator_smali() locates it the same way: by its
content (the literal "GameLaunchCoordinator" log tag plus an actual
startActivity call, since a sibling class references that same tag in
log messages without ever dispatching anything itself). Once found, every
startActivity call in that file is fair game to redirect, unlike
MainActivity, this class exists for no other purpose than coordinating
one game launch. find_main_activity_smali()/LOG_ANCHOR is tried first for
older iiSU builds that still have it; find_game_launch_coordinator_smali()
is the fallback once it's gone.
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
GAME_LAUNCH_COORDINATOR_LOG_TAG = "GameLaunchCoordinator"
# Register operands can be either v (local) or p (parameter) registers, a
# method that receives its Context/Intent as its own parameters (confirmed
# live in the GameLaunchCoordinator fallback path: a no-arg method still
# uses "p0" there, register reuse once the original "this" value is no
# longer needed is legal Dalvik bytecode) can pass them straight through
# to startActivity without ever copying them into locals first.
STARTACTIVITY_RE = re.compile(
    r"^(\s*)invoke-virtual \{([vp]\d+), ([vp]\d+)(?:, [vp]\d+)?\}, "
    r"Landroid/(?:content/Context|app/Activity);->startActivity\(Landroid/content/Intent;(?:Landroid/os/Bundle;)?\)V\s*$"
)
BRIDGE_PACKAGE_SMALI_DIR = "com/iisulauncher/pcbridge"


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    # Every command this wraps (java -jar apktool, zipalign, apksigner.bat)
    # is console-subsystem; this runs from the GUI's Setup flow
    # (pythonw.exe, no console of its own), so without CREATE_NO_WINDOW
    # each one flashes its own window during patching.
    kwargs.setdefault("creationflags", 0x08000000)
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({' '.join(args)}):\n{result.stdout}\n{result.stderr}")
    return result


def validate_iisu_apk(apk_path: Path) -> None:
    """Cheap pre-check that apk_path is actually iiSU, so a wrong file gets
    rejected in under a second instead of after a multi-GB SDK download and
    a full apktool decompile (the point where find_main_activity_smali()
    would otherwise be the first thing to notice). Searches the raw dex
    bytes for either of the two anchors the real patch can use (see
    module docstring), an ASCII string constant lands in the dex's string
    pool as contiguous UTF-8 bytes, so a plain byte search finds it
    without decompiling anything."""
    if not zipfile.is_zipfile(apk_path):
        raise RuntimeError(f"{apk_path} is not a valid APK (not a zip file).")

    anchor_candidates = [LOG_ANCHOR.encode("utf-8"), GAME_LAUNCH_COORDINATOR_LOG_TAG.encode("utf-8")]
    with zipfile.ZipFile(apk_path) as z:
        dex_names = [n for n in z.namelist() if re.fullmatch(r"classes\d*\.dex", n)]
        if not dex_names:
            raise RuntimeError(f"{apk_path.name} has no classes.dex, it doesn't look like a valid Android APK.")
        dex_contents = [z.read(name) for name in dex_names]
        found = any(anchor in content for content in dex_contents for anchor in anchor_candidates)

    if not found:
        raise RuntimeError(
            f"{apk_path.name} doesn't look like iiSU, couldn't find its ROM-launch code in it. "
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
    calls within that one method, not anywhere else in this very large
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
        raise RuntimeError(f"Anchor {LOG_ANCHOR!r} disappeared between the file-level check and patching, this shouldn't happen.")

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


def find_game_launch_coordinator_smali(decompiled_dir: Path) -> Path:
    """Fallback for iiSU builds where LOG_ANCHOR is gone (see module
    docstring): locates the single-purpose class that actually fires the
    game-launch startActivity call, by content rather than its obfuscated
    name, which changes every rebuild. A sibling class references the same
    "GameLaunchCoordinator" log tag in its own log messages without ever
    dispatching anything itself (confirmed: it has zero startActivity
    calls), so the tag alone isn't a unique-enough anchor on its own,
    requiring an actual startActivity call alongside it is."""
    candidates = []
    for path in decompiled_dir.rglob("*.smali"):
        text = path.read_text(encoding="utf-8", errors="replace")
        # STARTACTIVITY_RE is anchored with ^/$ against a single line (it's
        # normally matched per-line via .match()), not re.MULTILINE, so it
        # must be checked line by line here too, .search()'ing the whole
        # file's text as one blob would only ever match at the file's
        # very start/end and silently find nothing.
        if GAME_LAUNCH_COORDINATOR_LOG_TAG in text and any(STARTACTIVITY_RE.match(line) for line in text.splitlines()):
            candidates.append(path)
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one GameLaunchCoordinator-tagged class with a startActivity call, found {len(candidates)}. "
            "iiSU's code has likely changed and this patch needs updating by hand."
        )
    return candidates[0]


def patch_all_start_activity_calls(path: Path) -> int:
    """Redirects every startActivity(Intent[, Bundle]) call in path to
    LaunchBridge.launch(), with no method-scoping (unlike
    patch_main_activity()): this is only ever called on a class found by
    find_game_launch_coordinator_smali(), which exists for no purpose
    other than coordinating one game launch, so every startActivity call
    in it (the normal path and the StrictMode-relaxed cyou.joiplay.joiplay
    workaround path alike, confirmed live via a real 7.5-prerelease1
    launch) is fair game, unlike MainActivity's many unrelated ones."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

    patched = 0
    for i, line in enumerate(lines):
        match = STARTACTIVITY_RE.match(line)
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
            f"Found the GameLaunchCoordinator class ({path.name}) but no startActivity(Intent) "
            "call inside it to redirect, this shouldn't happen (the file-level check above "
            "already confirmed one exists)."
        )

    path.write_text("".join(lines), encoding="utf-8")
    return patched


PRIMARY_HOME_ACTIONS_METHOD_ANCHOR = ".method public final a()Lcom/iisulauncher/launcher/MainActivity;"
PRIMARY_HOME_ACTIONS_CLASS_RE = re.compile(r"^\.class public final L(\w+);$", re.MULTILINE)


def patch_primary_home_actions_holder(decompiled_dir: Path) -> tuple[Path, str]:
    """Expose iiSU's existing injected PrimaryHomeActions instance (called
    "je6" in the build this was first written against).

    This class already owns the synchronized WeakReference<MainActivity>.
    This patch exposes only the object itself through a static field so
    MediaBridge can call its a() method and reuse iiSU's existing activity
    lifecycle tracking.

    Anchored on class/method text rather than a hardcoded class name: R8
    reassigns these short obfuscated names on every rebuild (confirmed
    live, the same class is "dq6" as of iiSU Alpha 7.5-prerelease1), so
    the class name is read out of each candidate file instead of glob'd by
    filename, and reused for the field/sput text it injects. Idempotent.
    """
    candidates = []
    for path in decompiled_dir.rglob("*.smali"):
        text = path.read_text(encoding="utf-8", errors="replace")
        class_match = PRIMARY_HOME_ACTIONS_CLASS_RE.search(text)
        if class_match and PRIMARY_HOME_ACTIONS_METHOD_ANCHOR in text:
            candidates.append((path, class_match.group(1), text))

    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one iiSU PrimaryHomeActions holder class, found {len(candidates)}. "
            "iiSU's code has likely changed and this patch needs updating by hand."
        )

    path, class_name, text = candidates[0]
    class_ref = f"L{class_name};"

    static_field = f".field public static volatile c:{class_ref}"
    if static_field not in text:
        field_anchor = "# instance fields"
        if field_anchor not in text:
            raise RuntimeError(f"Found {path.name} but could not find its instance-fields anchor.")
        text = text.replace(
            field_anchor,
            "# iiSU-PC: expose this existing injected PrimaryHomeActions holder.\n"
            f"{static_field}\n\n"
            f"{field_anchor}",
            1,
        )

    sput = f"    sput-object p0, {class_ref}->c:{class_ref}"
    if sput not in text:
        ctor_start = text.find(".method public constructor <init>()V")
        if ctor_start < 0:
            raise RuntimeError(f"Found {path.name} but could not find its constructor.")

        ctor_end = text.find(".end method", ctor_start)
        if ctor_end < 0:
            raise RuntimeError(f"Found {path.name}'s constructor but not its .end method.")

        ctor = text[ctor_start:ctor_end]
        super_call = "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V"
        if super_call not in ctor:
            raise RuntimeError(f"Found {path.name}'s constructor but could not find its Object constructor call.")

        patched_ctor = ctor.replace(
            super_call,
            super_call
            + "\n\n"
            + "    # iiSU-PC: expose this same Hilt-created holder; MainActivity itself\n"
            + "    # remains referenced only by this class's existing WeakReference.\n"
            + sput,
            1,
        )
        text = text[:ctor_start] + patched_ctor + text[ctor_end:]

    path.write_text(text, encoding="utf-8")
    return path, class_name

def inject_launch_bridge(decompiled_dir: Path, primary_home_actions_class: str) -> None:
    """Copies smali_patch/*.smali into the decompiled tree, substituting the
    placeholder "je6" class reference MediaBridgeReceiver.smali is written
    against for whichever class patch_primary_home_actions_holder() actually
    found this build (that name changes every rebuild, see its own
    docstring), so MediaBridgeReceiver keeps pointing at a class that
    genuinely exists in the patched APK instead of silently referencing one
    that doesn't."""
    dest = decompiled_dir / "smali" / BRIDGE_PACKAGE_SMALI_DIR
    dest.mkdir(parents=True, exist_ok=True)
    for smali_file in SMALI_PATCH_DIR.glob("*.smali"):
        text = smali_file.read_text(encoding="utf-8")
        text = text.replace("Lje6;", f"L{primary_home_actions_class};")
        (dest / smali_file.name).write_text(text, encoding="utf-8")


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
    APK is re-signed with a different key than the original, flip it."""
    manifest = decompiled_dir / "AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace('android:extractNativeLibs="false"', 'android:extractNativeLibs="true"'), encoding="utf-8")


def zipalign(zipalign_exe: Path, unsigned_apk: Path, aligned_apk: Path) -> None:
    run([str(zipalign_exe), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)])


def sign(apksigner_exe: Path, keystore: Path, keystore_pass: str, key_alias: str, aligned_apk: Path, signed_apk: Path) -> None:
    # apksigner.bat resolves its own `java` via JAVA_HOME/PATH internally,
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
    try:
        main_activity = find_main_activity_smali(decompiled_dir)
        patched_count = patch_main_activity(main_activity)
    except RuntimeError:
        # LOG_ANCHOR is gone as of iiSU Alpha 7.5-prerelease1, which moved
        # the actual dispatch out of MainActivity into its own dedicated
        # class (see module docstring). Older builds still hit the try
        # above and never reach this fallback.
        main_activity = find_game_launch_coordinator_smali(decompiled_dir)
        patched_count = patch_all_start_activity_calls(main_activity)
    print(f"[patch] redirected {patched_count} startActivity call(s) to LaunchBridge in {main_activity.relative_to(decompiled_dir)}")

    print("[patch] exposing iiSU's existing PrimaryHomeActions holder...")
    primary_home_actions, primary_home_actions_class = patch_primary_home_actions_holder(decompiled_dir)
    print(f"[patch] patched PrimaryHomeActions holder ({primary_home_actions_class}) in {primary_home_actions.relative_to(decompiled_dir)}")

    print("[patch] injecting iiSU-PC bridge classes...")
    inject_launch_bridge(decompiled_dir, primary_home_actions_class)
    patch_manifest_for_media_bridge(decompiled_dir)
    fix_extract_native_libs(decompiled_dir)

    print("[patch] rebuilding...")
    build(decompiled_dir, unsigned_apk)

    print("[patch] aligning and signing...")
    zipalign(zipalign_exe, unsigned_apk, aligned_apk)
    sign(apksigner_exe, keystore, keystore_pass, key_alias, aligned_apk, output_apk)

    print(f"[patch] done: {output_apk}")
