"""Shown by the Manager when bridge/config.json exists but can't be read
(see bridge_config.ConfigInvalidError): says what is wrong in plain words and
offers to put back the last settings that worked, or to start from default
settings. Either way the damaged file is kept beside it under a new name, so
nothing is thrown away.

Standard message boxes on purpose: they are read out by screen readers, every
choice is a labelled button reachable with Tab, and Escape always means
"leave everything as it is".
"""

from PySide6.QtWidgets import QMessageBox, QWidget

import bridge_config
from shared.platform_compat import open_uri

TITLE = "Settings file is damaged"


def _kept_note(kept) -> str:
    return f"\n\nThe damaged file was kept as {kept.name}, in the same folder." if kept else ""


def run_config_recovery(parent: QWidget | None, reason: str) -> bool:
    """Returns True when config.json was replaced with something readable."""
    has_backup = bridge_config.has_usable_backup()
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(TITLE)
    box.setText("Community-iiSU-PC can't read its settings file.")
    box.setInformativeText(
        f"{bridge_config.CONFIG_PATH.name} is damaged ({reason}). This can happen when the PC "
        "loses power or a drive fills up while settings are being saved, or after editing the "
        "file by hand.\n\n"
        "Your Android VM, iiSU and your games are not affected."
        + (
            "\n\nRestore puts back the settings from your last successful save."
            if has_backup else
            "\n\nThere is no earlier copy to restore, so the settings can be reset to their "
            "defaults. You will need to choose your ROM folder and emulator folders again."
        )
    )
    restore = box.addButton("Restore Last Working Settings", QMessageBox.ButtonRole.AcceptRole) if has_backup else None
    reset = box.addButton("Reset to Default Settings", QMessageBox.ButtonRole.DestructiveRole)
    folder = box.addButton("Open Settings Folder", QMessageBox.ButtonRole.ActionRole)
    later = box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(restore or later)
    box.setEscapeButton(later)
    box.exec()
    clicked = box.clickedButton()

    if clicked is folder:
        open_uri(str(bridge_config.CONFIG_PATH.parent))
        return False
    try:
        if restore is not None and clicked is restore:
            kept = bridge_config.restore_backup()
            QMessageBox.information(parent, TITLE, "Your last working settings are back." + _kept_note(kept))
            return True
        if clicked is reset:
            confirm = QMessageBox.question(
                parent, TITLE,
                "Reset every setting to its default?\n\n"
                "You will need to choose your ROM folder and emulator folders again, and any "
                "changed emulator mappings, hotkeys and display settings go back to their defaults.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return False
            kept = bridge_config.reset_to_defaults()
            QMessageBox.information(
                parent, TITLE,
                "Settings were reset. Open Library > ROM Directory to choose your ROM folder and "
                "emulator folders." + _kept_note(kept),
            )
            return True
    except (OSError, ValueError) as e:
        QMessageBox.critical(parent, TITLE, f"That didn't work:\n\n{e}")
    return False
