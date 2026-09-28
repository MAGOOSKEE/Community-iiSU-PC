"""
Unit tests for bridge/emulator_downloader.py's catalog and pure logic,
no real package manager or network access needed.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from emulator_downloader import (
    EMULATOR_CATALOG,
    detect_backend,
    get_catalog_with_status,
    install_emulator,
    is_emulator_installed,
)


class EmulatorDownloaderTests(unittest.TestCase):
    def test_catalog_entries_have_required_fields(self):
        self.assertGreater(len(EMULATOR_CATALOG), 5)
        for emu in EMULATOR_CATALOG:
            self.assertIn("id", emu)
            self.assertIn("name", emu)
            self.assertIn("systems", emu)
            self.assertIn("check_names", emu)
            self.assertTrue(emu["check_names"])

    def test_detect_backend_returns_a_known_value(self):
        backend, _info = detect_backend()
        self.assertIn(backend, ("flatpak", "winget", "none"))

    @patch("emulator_downloader.shutil.which")
    def test_is_emulator_installed_via_which(self, mock_which):
        mock_which.side_effect = lambda name: "/usr/bin/x" if name == "found_binary" else None
        self.assertTrue(is_emulator_installed({"check_names": ["found_binary"]}))
        self.assertFalse(is_emulator_installed({"check_names": ["nonexistent_binary_xyz"]}))

    def test_get_catalog_with_status_marks_every_entry(self):
        catalog = get_catalog_with_status()
        self.assertEqual(len(catalog), len(EMULATOR_CATALOG))
        for item in catalog:
            self.assertIn("installed", item)
            self.assertIsInstance(item["installed"], bool)

    @patch("emulator_downloader.detect_backend", return_value=("flatpak", "/usr/bin/flatpak"))
    @patch("emulator_downloader.ensure_flathub_remote")
    @patch("emulator_downloader.subprocess.Popen")
    def test_install_emulator_flatpak_success(self, mock_popen, mock_ensure_remote, mock_detect):
        mock_process = MagicMock()
        mock_process.stdout = ["Downloading...\n", "Installing...\n"]
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        output_lines = []
        emu = {"name": "DuckStation", "flatpak_id": "org.duckstation.DuckStation"}
        ok, err = install_emulator(emu, on_output=output_lines.append)

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertIn("Downloading...\n", output_lines)

    @patch("emulator_downloader.detect_backend", return_value=("none", "no package manager available"))
    def test_install_emulator_with_no_backend(self, mock_detect):
        ok, err = install_emulator({"name": "DuckStation"})
        self.assertFalse(ok)
        self.assertEqual(err, "no package manager available")


if __name__ == "__main__":
    unittest.main()
