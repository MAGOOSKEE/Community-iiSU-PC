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

BRIDGE_DIR = Path(__file__).parent.parent / "bridge"
SCRIPTS = ("launch_bridge", "stop_iisu_pc", "sync_library", "start_iisu_pc")


class RawScriptImportTests(unittest.TestCase):
    def test_each_script_imports_with_only_its_own_folder_on_the_path(self):
        for name in SCRIPTS:
            with self.subTest(script=name):
                code = f"import sys; sys.path.insert(0, r'{BRIDGE_DIR}'); import {name}"
                env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
                with tempfile.TemporaryDirectory() as elsewhere:  # cwd must not be the project root
                    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=elsewhere, env=env, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr[-600:])


if __name__ == "__main__":
    unittest.main()
