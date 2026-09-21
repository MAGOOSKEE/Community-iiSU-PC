"""
Loads bridge/config.json for the other bridge/ scripts.

config.json is created by installer/setup_wizard.py during first-time
setup -- it doesn't exist until Setup.bat has been run once. Centralizing
the load here means every entry point (manager.py's Home page and
settings pages, launch_bridge.py, apply_display.py) reports that as a
clear, actionable message instead of each raising its own raw
FileNotFoundError.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


class ConfigMissingError(RuntimeError):
    def __init__(self):
        super().__init__(
            f"{CONFIG_PATH} not found. Run Setup.bat first to complete "
            "first-time setup -- it creates this file for you."
        )


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        raise ConfigMissingError()
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)
