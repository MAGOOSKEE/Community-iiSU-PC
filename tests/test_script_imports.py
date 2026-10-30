"""The bridge scripts are launched as raw scripts (subprocess.Popen([python, "x.py"])), which
puts only bridge/ on sys.path, not the project root where shared/ lives. A dev install once
crashed the launch bridge at startup with "No module named 'shared'" because of that; importing
each script the way a raw launch would, in a clean interpreter, keeps it from coming back."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
BRIDGE_DIR = PROJECT_ROOT / "bridge"
SCRIPTS = ("launch_bridge", "stop_iisu_pc", "sync_library", "start_iisu_pc")
# Run as `python <file>` by an installed copy: Setup from the command line, and
# the installer's uninstall step (CommunityIisuPC.iss runs uninstall.py).
STANDALONE_SCRIPTS = (BRIDGE_DIR / "start_iisu_pc.py", BRIDGE_DIR / "stop_iisu_pc.py",
                      BRIDGE_DIR / "launch_bridge.py", PROJECT_ROOT / "installer" / "setup_wizard.py",
                      PROJECT_ROOT / "installer" / "uninstall.py")


class RawScriptImportTests(unittest.TestCase):
    def test_each_script_imports_with_only_its_own_folder_on_the_path(self):
        for name in SCRIPTS:
            with self.subTest(script=name):
                code = f"import sys; sys.path.insert(0, r'{BRIDGE_DIR}'); import {name}"
                env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
                with tempfile.TemporaryDirectory() as elsewhere:  # cwd must not be the project root
                    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=elsewhere, env=env, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr[-600:])

    def test_each_script_loads_without_its_own_folder_on_the_path(self):
        """The bundled runtime (runtime/python) doesn't add a script's folder to
        sys.path at all; -P makes any Python behave that way. Loading the file
        runs its imports but not its main()."""
        for path in STANDALONE_SCRIPTS:
            with self.subTest(script=path.name):
                code = f"import runpy; runpy.run_path(r'{path}', run_name='import_check')"
                env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}
                with tempfile.TemporaryDirectory() as elsewhere:
                    result = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True, cwd=elsewhere, env=env, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr[-600:])


if __name__ == "__main__":
    unittest.main()
