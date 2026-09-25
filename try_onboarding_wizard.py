"""
Quick manual test: opens the new Qt onboarding wizard for real interaction.

Safe to click all the way through, including "Finish", this redirects
config saves to a temp file, NOT your real bridge/config.json, so there's
no risk to your actual install no matter what you do in here.

Delete this file whenever; it's just a throwaway test script, not part of
the app.

Run with:
    python try_onboarding_wizard.py
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication

from shared.qt_theme import apply_theme
from bridge.ui import onboarding_wizard as ow

tmp_dir = tempfile.TemporaryDirectory()
fake_config_path = Path(tmp_dir.name) / "config.json"
print(f"Safety: config saves in this test go to {fake_config_path}, not your real config.json.")

app = QApplication(sys.argv)
apply_theme(app)

with patch.object(ow, "CONFIG_PATH", fake_config_path):
    window = ow.OnboardingWizard()
    window.show()
    sys.exit(app.exec())
