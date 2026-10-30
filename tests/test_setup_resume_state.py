"""Unit tests for setup_wizard.forget_resume_state: every Setup, iiSU update or
rebuild boot changes the VM's disk, so it must drop the quick-resume snapshot
and the boot fingerprint. A re-run Setup used to leave both, and the next start
tried to resume memory from before Setup. Temporary folders only."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "installer"))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

import portable_sdk  # noqa: E402; path set up above
import setup_wizard  # noqa: E402; path set up above


class ForgetResumeStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.avd_home = root / "avd-home"
        self.bridge = root / "bridge"
        self.bridge.mkdir()
        self.snapshot = self.avd_home / "iisuwin.avd" / "snapshots" / "default_boot"
        self.snapshot.mkdir(parents=True)
        for name in portable_sdk.QUICKBOOT_SNAPSHOT_FILES:
            (self.snapshot / name).write_bytes(b"x")
        self.disk = self.avd_home / "iisuwin.avd" / "userdata-qemu.img.qcow2"
        self.disk.write_bytes(b"data")
        self.fingerprint = self.bridge / ".boot_fingerprint.json"
        self.fingerprint.write_text("{}", encoding="utf-8")

    def _forget(self):
        with patch.object(portable_sdk, "PORTABLE_AVD_HOME", self.avd_home), \
             patch.object(setup_wizard, "BRIDGE_DIR", self.bridge):
            setup_wizard.forget_resume_state("iisuwin")

    def test_drops_the_snapshot_and_the_fingerprint_but_not_the_disk(self):
        self._forget()
        self.assertFalse(self.snapshot.exists())
        self.assertFalse(self.fingerprint.exists())
        self.assertTrue(self.disk.is_file())

    def test_nothing_to_drop_is_fine(self):
        self._forget()
        self._forget()


if __name__ == "__main__":
    unittest.main()
