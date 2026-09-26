"""Resolves the Java tooling a subprocess call should use, shared by
patch_iisu.py's/stub_apk.py's/create_shortcut.py's apktool, keytool, and
apksigner calls, all of which shell out to Java tooling.

Today (running from source, no installer build yet) there's no bundled
JRE, so every call site keeps falling back to whatever `java`/`keytool`/
apksigner.bat's own internal Java resolution finds on the system PATH,
exactly as before. Once the Inno Setup build ships a jlink-trimmed JRE
under {app}\\runtime\\jre, java_exe()/keytool_exe() start returning that
JRE's own bin/ executables by absolute path, and java_subprocess_env()
starts returning an environment with that JRE's bin/ prepended and
JAVA_HOME set.

A real user (see the "wrong java runtime" GitHub issue/Discord thread)
had an ancient 32-bit Java 8 shadowing their real JDK on PATH, which is
exactly the class of bug an absolute path sidesteps rather than merely
reduces: passing env=java_subprocess_env() alone changes the *child's*
PATH, but on Windows a bare command name like "java" is resolved by
CreateProcess against the *calling* process's own PATH before the new
environment ever takes effect, so it doesn't reliably override which
java.exe gets picked when we're the ones launching java directly (as
opposed to apksigner.bat, which launches its own "java" internally, at
which point apksigner.bat's env genuinely is the active process
environment). java_exe()/keytool_exe() close that gap for the sites that
launch java directly, java_subprocess_env() stays for apksigner.bat,
which still needs it. Deliberately never touches the user's real system
PATH/JAVA_HOME, only the subprocess's own copy of the environment.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_JRE_DIR = PROJECT_ROOT / "runtime" / "jre"


def _bundled_bin() -> Path | None:
    jre_bin = BUNDLED_JRE_DIR / "bin"
    return jre_bin if jre_bin.is_dir() else None


def java_exe(name: str = "java") -> str:
    """Absolute path to the bundled JRE's java.exe/keytool.exe, or just
    `name` unchanged (falls back to PATH-based resolution) when there's
    no bundled JRE, e.g. running from a source checkout."""
    jre_bin = _bundled_bin()
    if jre_bin is None:
        return name
    exe = jre_bin / f"{name}.exe"
    return str(exe) if exe.is_file() else name


def keytool_exe() -> str:
    return java_exe("keytool")


def java_subprocess_env() -> dict[str, str] | None:
    """Returns an environment dict for a Java-invoking subprocess call, or
    None to mean "just inherit the current environment unchanged" (the
    subprocess module's own default when env=None). Mainly for
    apksigner.bat, which resolves its own `java` internally, see module
    docstring for why direct java_exe()/keytool_exe() calls need more
    than this alone."""
    jre_bin = _bundled_bin()
    if jre_bin is None:
        return None
    env = os.environ.copy()
    env["JAVA_HOME"] = str(BUNDLED_JRE_DIR)
    env["PATH"] = str(jre_bin) + os.pathsep + env.get("PATH", "")
    return env
