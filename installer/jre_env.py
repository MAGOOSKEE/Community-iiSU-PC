"""Resolves the environment a Java-invoking subprocess call should use,
shared by patch_iisu.py's/stub_apk.py's apksigner calls and
create_shortcut.py's apktool invocation, all three of which shell out to
Java tooling.

Today (running from source, no installer build yet) this is a no-op:
there's no bundled JRE, so every call site keeps using whatever `java`/
`keytool`/apksigner.bat's own internal Java resolution already finds on
the system PATH, exactly as before. Once the Inno Setup build (Phase 3-4
of the Qt rewrite plan) starts shipping a jlink-trimmed JRE under
{app}\\runtime\\jre, this starts returning an environment with that JRE's
bin/ prepended and JAVA_HOME set, apksigner.bat in particular resolves
its own `java` via JAVA_HOME/PATH internally and won't find a bundled JRE
otherwise. Deliberately never touches the user's real system PATH/
JAVA_HOME, only the subprocess's own copy of the environment.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_JRE_DIR = PROJECT_ROOT / "runtime" / "jre"


def java_subprocess_env() -> dict[str, str] | None:
    """Returns an environment dict for a Java-invoking subprocess call, or
    None to mean "just inherit the current environment unchanged" (the
    subprocess module's own default when env=None)."""
    jre_bin = BUNDLED_JRE_DIR / "bin"
    if not jre_bin.is_dir():
        return None
    env = os.environ.copy()
    env["JAVA_HOME"] = str(BUNDLED_JRE_DIR)
    env["PATH"] = str(jre_bin) + os.pathsep + env.get("PATH", "")
    return env
