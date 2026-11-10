"""
Loads and saves bridge/config.json for the other bridge/ scripts.

config.json is created by installer/setup_wizard.py during first-time
setup, it doesn't exist until Setup.bat has been run once. Centralizing
the load here means every entry point (the Manager's Home page and
settings pages, launch_bridge.py, apply_display.py) reports that as a
clear, actionable message instead of each raising its own raw
FileNotFoundError.

A config.json that exists but can't be read as settings (cut short by a
crash or a full disk mid-save, or a hand edit with a stray comma) is its own
case, ConfigInvalidError. It used to surface as a raw JSON traceback in the
start and bridge logs, and the Manager took it for "not set up yet" and
offered to run Setup over a working install. Now every save goes through a
temporary file, so a save can't be cut short, the last settings that loaded
are kept as config.json.bak, and the Manager offers to put that copy back or
start from default settings (see bridge/ui/dialogs/config_recovery_dialog.py).
"""

import json
import os
import shutil
import time
from pathlib import Path

from shared.emulator_defaults import build_emulators_map, upgrade_outdated_defaults

CONFIG_PATH = Path(__file__).parent / "config.json"
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "installer" / "config.template.json"
AVD_HOME = Path(__file__).parent / "android-sdk-portable" / "avd-home"


def backup_path() -> Path:
    """Beside whatever CONFIG_PATH is right now, never fixed at import: a test
    that points CONFIG_PATH at a temporary file must not leave its backup in
    the real bridge folder."""
    return CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak")


class ConfigMissingError(RuntimeError):
    def __init__(self):
        super().__init__(
            f"{CONFIG_PATH} not found. Run Setup.bat first to complete "
            "first-time setup, it creates this file for you."
        )


class ConfigInvalidError(ConfigMissingError):
    """config.json is there but isn't readable settings. A kind of
    ConfigMissingError so every script that already stops cleanly on a
    missing config stops the same way on a damaged one, with this message."""

    def __init__(self, reason: str, path: Path | None = None):
        self.reason = reason
        self.path = path or CONFIG_PATH
        RuntimeError.__init__(
            self,
            f"{self.path} is damaged and can't be read ({reason}). Open the Community-iiSU-PC "
            "Manager to restore the last working copy or reset the settings.",
        )


def read_config_file(path: Path) -> dict:
    """The settings in one config file, raising ConfigInvalidError saying
    where the problem is when it isn't a JSON object."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ConfigInvalidError("it isn't text", path) from None
    if not text.strip():
        raise ConfigInvalidError("the file is empty", path)
    try:
        config = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigInvalidError(f"line {e.lineno}, column {e.colno}: {e.msg}", path) from None
    if not isinstance(config, dict):
        raise ConfigInvalidError("it doesn't hold a list of settings", path)
    return config


def load_config() -> dict:
    """The config, with any emulator default an earlier version got wrong and
    nobody has edited since replaced by the fixed one (see
    shared/emulator_defaults.OUTDATED_DEFAULTS). Saving writes the fix back."""
    if not CONFIG_PATH.is_file():
        raise ConfigMissingError()
    config = read_config_file(CONFIG_PATH)
    if isinstance(config.get("emulators"), dict):
        config["emulators"] = upgrade_outdated_defaults(config["emulators"])
    return config


def config_problem() -> str | None:
    """Why config.json can't be read, or None when it is fine or simply not
    there yet (a fresh install)."""
    if not CONFIG_PATH.is_file():
        return None
    try:
        read_config_file(CONFIG_PATH)
    except ConfigInvalidError as e:
        return e.reason
    except OSError as e:
        return str(e)
    return None


def write_config_file(path: Path, config: dict) -> None:
    """Writes the settings to a temporary file beside `path` and swaps it in,
    so a crash, a power cut or a full disk partway leaves the old file whole
    instead of half of the new one."""
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(temporary, path)


def save_config(config: dict) -> None:
    """Saves the settings, first keeping the ones being replaced as
    config.json.bak when they still load, so there is a working copy to go
    back to."""
    if CONFIG_PATH.is_file():
        try:
            read_config_file(CONFIG_PATH)
            shutil.copyfile(CONFIG_PATH, backup_path())
        except (ConfigInvalidError, OSError):
            pass  # never replace a good backup with a damaged file
    write_config_file(CONFIG_PATH, config)


def has_usable_backup() -> bool:
    try:
        read_config_file(backup_path())
    except (ConfigInvalidError, OSError):
        return False
    return True


def set_aside_damaged() -> Path | None:
    """Moves the unreadable config.json out of the way, keeping it so nothing
    is lost: whatever it still holds can be copied out by hand. Returns where
    it went, or None if there was nothing to move."""
    if not CONFIG_PATH.is_file():
        return None
    target = CONFIG_PATH.with_name(f"config.damaged-{time.strftime('%Y%m%d-%H%M%S')}.json")
    os.replace(CONFIG_PATH, target)
    return target


def restore_backup() -> Path | None:
    """Puts config.json.bak back as config.json. Returns where the damaged
    file was kept. Raises ConfigInvalidError if the backup is damaged too."""
    config = read_config_file(backup_path())
    kept = set_aside_damaged()
    write_config_file(CONFIG_PATH, config)
    return kept


def default_config() -> dict:
    """Settings for an install whose config can't be recovered: the same
    template Setup starts from, the default emulator mappings, and the
    Android VM this install already has, so the VM, iiSU and everything in
    it carry on. The ROM folder and emulator folders have to be chosen again."""
    config = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    config["emulators"] = build_emulators_map()
    avds = sorted(path.stem for path in AVD_HOME.glob("*.avd") if path.is_dir()) if AVD_HOME.is_dir() else []
    if len(avds) == 1:
        config["avd_name"] = avds[0]
        config["iisu_window_title"] = avds[0]
        config["vm_backend"] = "avd"
    return config


def reset_to_defaults() -> Path | None:
    """Replaces an unreadable config.json with default_config(). Returns
    where the damaged file was kept."""
    config = default_config()
    kept = set_aside_damaged()
    write_config_file(CONFIG_PATH, config)
    return kept
