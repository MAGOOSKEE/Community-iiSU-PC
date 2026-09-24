"""Builds the jlink-trimmed JRE the Inno Setup installer bundles, at
{project_root}/runtime/jre -- exactly the path installer/jre_env.py checks
for. Run this once per release on a build machine with a real JDK
installed (any recent Temurin/OpenJDK build works); the *installed* app
never needs a full JDK, only this trimmed output.

Module list is derived mechanically via jdeps against apktool.jar (the
only Java-invoked artifact this project ships) rather than hand-picked,
then verified by actually running both apktool and keytool against the
trimmed image -- jdeps against apktool alone doesn't fully capture
keytool's own dependency needs (see the Qt rewrite plan's Phase 3-4 notes),
so both get a real smoke test below, not just a jlink build with no
verification.

Usage:
    python installer/build_jre.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APKTOOL_JAR = PROJECT_ROOT / "installer" / "tools" / "apktool.jar"
OUTPUT_DIR = PROJECT_ROOT / "runtime" / "jre"

# java.base is implied by jlink but named explicitly anyway so the
# --add-modules list here matches what a human re-running this by hand
# would naturally type, rather than relying on an undocumented default.
EXTRA_MODULES = ["java.base"]


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({' '.join(args)}):\n{result.stdout}\n{result.stderr}")
    return result


def require_jdk_tools() -> None:
    missing = [tool for tool in ("java", "jlink", "jdeps", "keytool") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            f"Missing JDK tool(s) on PATH: {', '.join(missing)}. This script needs a full JDK "
            "installed on the build machine (e.g. Temurin) -- the trimmed output it produces is "
            "what actually ships, not this JDK itself."
        )


def detect_required_modules() -> list[str]:
    if not APKTOOL_JAR.is_file():
        raise RuntimeError(f"apktool.jar not found at {APKTOOL_JAR}")
    print(f"[build_jre] running jdeps against {APKTOOL_JAR.name}...")
    result = run(["jdeps", "--print-module-deps", "--ignore-missing-deps", str(APKTOOL_JAR)])
    modules = sorted(set(result.stdout.strip().split(",")) | set(EXTRA_MODULES))
    print(f"[build_jre] modules: {', '.join(modules)}")
    return modules


def build_trimmed_jre(modules: list[str]) -> None:
    if OUTPUT_DIR.exists():
        print(f"[build_jre] removing previous build at {OUTPUT_DIR}...")
        shutil.rmtree(OUTPUT_DIR)
    print(f"[build_jre] running jlink -> {OUTPUT_DIR}...")
    run([
        "jlink",
        "--add-modules", ",".join(modules),
        "--output", str(OUTPUT_DIR),
        "--no-header-files",
        "--no-man-pages",
        "--strip-debug",
        "--compress=zip-9",
    ])


def verify_apktool() -> None:
    java_exe = OUTPUT_DIR / "bin" / "java.exe"
    print("[build_jre] verifying apktool runs against the trimmed JRE...")
    result = run([str(java_exe), "-jar", str(APKTOOL_JAR), "--version"])
    print(f"[build_jre] apktool OK (version {result.stdout.strip()})")


def verify_keytool() -> None:
    keytool_exe = OUTPUT_DIR / "bin" / "keytool.exe"
    print("[build_jre] verifying keytool -genkeypair -keyalg RSA against the trimmed JRE...")
    with tempfile.TemporaryDirectory() as tmp:
        keystore = Path(tmp) / "verify.jks"
        run([
            str(keytool_exe), "-genkeypair", "-v",
            "-keystore", str(keystore),
            "-alias", "verify",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "1",
            "-storepass", "verify-only", "-keypass", "verify-only",
            "-dname", "CN=Verify, OU=Verify, O=Verify, L=Local, S=Local, C=US",
        ])
        if not keystore.is_file():
            raise RuntimeError("keytool reported success but produced no keystore file")
    print("[build_jre] keytool OK")


def main() -> None:
    require_jdk_tools()
    modules = detect_required_modules()
    build_trimmed_jre(modules)
    verify_apktool()
    verify_keytool()
    size_mb = sum(f.stat().st_size for f in OUTPUT_DIR.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"[build_jre] done: {OUTPUT_DIR} ({size_mb:.0f} MB)")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"[build_jre] FAILED: {e}", file=sys.stderr)
        sys.exit(1)
