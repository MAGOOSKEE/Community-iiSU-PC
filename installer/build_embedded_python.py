"""Builds the embeddable CPython runtime the Inno Setup installer bundles,
at {project_root}/runtime/python, the path the installer's Start Menu
shortcut launches via pythonw.exe -m bridge.ui.app. Run this once per
release on a build machine with internet access; the *installed* app
never needs Python pre-installed, only this runtime.

Downloads python.org's official embeddable zip (not a full installer,
no venv/tkinter/idle, ~15MB), enables `import site` in its `._pth` file
(disabled by default in the embeddable distribution, which would
otherwise make pip-installed packages unimportable), bootstraps pip via
the standard get-pip.py, then pip-installs this project's two runtime
dependencies (PySide6, Pillow, see installer/setup_wizard.py's
ensure_pyside6()/ensure_pillow(), the same two packages that script
installs into a system Python today) directly into the runtime.

PYTHON_VERSION should track whatever .github/workflows/tests.yml's
python-version: "3.11" resolves to on GitHub's windows-latest runners;
bump it here when that meaningfully drifts (e.g. a new 3.11.x patch),
there's no way to resolve "3.11" to an exact patch mechanically without
also pulling in the whole actions/setup-python toolchain.

Usage:
    python installer/build_embedded_python.py
"""

import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "runtime" / "python"

PYTHON_VERSION = "3.11.9"
EMBED_ZIP_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Mirrors installer/setup_wizard.py's ensure_pyside6()/ensure_pillow(),
# same two packages, same "no pinned version" approach that script already
# uses for a system Python install.
RUNTIME_PACKAGES = ["PySide6", "Pillow"]


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({' '.join(args)}):\n{result.stdout}\n{result.stderr}")
    return result


def download(url: str, dest: Path) -> None:
    print(f"[build_embedded_python] downloading {url}...")
    urllib.request.urlretrieve(url, dest)


def extract_embeddable_zip(zip_path: Path) -> None:
    if OUTPUT_DIR.exists():
        print(f"[build_embedded_python] removing previous build at {OUTPUT_DIR}...")
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    print(f"[build_embedded_python] extracting -> {OUTPUT_DIR}...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(OUTPUT_DIR)


def enable_import_site() -> Path:
    """The embeddable distribution ships a python311._pth file that
    comments out `#import site` by default, which blocks anything
    pip-installs from being importable (site.py is what wires up
    site-packages). Uncommenting it is the one documented, required step
    for turning the embeddable zip into a runtime that can use pip at all.

    The ._pth file also *replaces* Python's normal sys.path setup rather
    than extending it, confirmed by testing directly against this
    build: neither `python -c "import bridge"` nor `python -m bridge.x`
    picks up the current working directory the way a regular install
    does. So this also appends "..\\.." (this project's own app source,
    relative to runtime/python's planned install location two levels
    down from {app} per the Inno Setup layout: {app}\\runtime\\python and
    {app}\\bridge/installer/shared as siblings of {app}) so `pythonw.exe
    -m bridge.ui.app` can find the app's own packages regardless of
    where the installer places {app}."""
    pth_files = list(OUTPUT_DIR.glob("python3*._pth"))
    if len(pth_files) != 1:
        raise RuntimeError(f"expected exactly one python3*._pth file in {OUTPUT_DIR}, found {pth_files}")
    pth_path = pth_files[0]
    print(f"[build_embedded_python] enabling 'import site' and app-root path in {pth_path.name}...")
    text = pth_path.read_text(encoding="utf-8")
    patched = text.replace("#import site", "import site")
    if patched == text:
        raise RuntimeError(f"{pth_path.name} didn't contain the expected '#import site' line, check its contents")
    patched = patched.replace("python311.zip\n", "python311.zip\n..\\..\n", 1)
    pth_path.write_text(patched, encoding="utf-8")
    return pth_path


def bootstrap_pip(python_exe: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        get_pip = Path(tmp) / "get-pip.py"
        download(GET_PIP_URL, get_pip)
        print("[build_embedded_python] bootstrapping pip...")
        run([str(python_exe), str(get_pip), "--no-warn-script-location"])


def install_runtime_packages(python_exe: Path) -> None:
    print(f"[build_embedded_python] installing {', '.join(RUNTIME_PACKAGES)}...")
    run([str(python_exe), "-m", "pip", "install", "--no-warn-script-location", *RUNTIME_PACKAGES])


def verify_imports(python_exe: Path) -> None:
    print("[build_embedded_python] verifying PySide6 and Pillow import in the embedded runtime...")
    run([str(python_exe), "-c", "import PySide6; import PIL; print('ok')"])
    print("[build_embedded_python] imports OK")


def verify_app_module_resolves(python_exe: Path) -> None:
    """Runs from inside runtime/python itself (cwd != project root) to
    prove the "..\\.." entry added in enable_import_site() is what's
    actually making bridge.ui resolve, not some cwd side effect of how
    this build script happens to be invoked."""
    print("[build_embedded_python] verifying bridge.ui.app resolves regardless of cwd...")
    run([str(python_exe), "-c", "import bridge.ui.main_window; print('ok')"], cwd=str(python_exe.parent))
    print("[build_embedded_python] app module resolution OK")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "embed.zip"
        download(EMBED_ZIP_URL, zip_path)
        extract_embeddable_zip(zip_path)

    enable_import_site()
    python_exe = OUTPUT_DIR / "python.exe"
    bootstrap_pip(python_exe)
    install_runtime_packages(python_exe)
    verify_imports(python_exe)
    verify_app_module_resolves(python_exe)

    size_mb = sum(f.stat().st_size for f in OUTPUT_DIR.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"[build_embedded_python] done: {OUTPUT_DIR} ({size_mb:.0f} MB)")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"[build_embedded_python] FAILED: {e}", file=sys.stderr)
        sys.exit(1)
