"""Unit tests for installer/jre_env.py."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

import jre_env


class JavaSubprocessEnvTests(unittest.TestCase):
    def test_returns_none_when_no_bundled_jre(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(jre_env, "BUNDLED_JRE_DIR", Path(tmp) / "does_not_exist"):
                self.assertIsNone(jre_env.java_subprocess_env())

    def test_returns_env_with_jre_bin_prepended_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            jre_dir = Path(tmp) / "jre"
            (jre_dir / "bin").mkdir(parents=True)
            with patch.object(jre_env, "BUNDLED_JRE_DIR", jre_dir):
                env = jre_env.java_subprocess_env()
            self.assertIsNotNone(env)
            self.assertEqual(env["JAVA_HOME"], str(jre_dir))
            self.assertTrue(env["PATH"].startswith(str(jre_dir / "bin")))

    def test_never_mutates_real_process_environment(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            jre_dir = Path(tmp) / "jre"
            (jre_dir / "bin").mkdir(parents=True)
            original_path = os.environ.get("PATH", "")
            with patch.object(jre_env, "BUNDLED_JRE_DIR", jre_dir):
                jre_env.java_subprocess_env()
            self.assertEqual(os.environ.get("PATH", ""), original_path)


if __name__ == "__main__":
    unittest.main()
