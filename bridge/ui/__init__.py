"""
The Qt rewrite of Community-iiSU-PC's GUI (see
C:\\Users\\Jaemin\\.claude\\plans\\robust-giggling-thompson.md), replacing
the flat, non-package bridge/*.py tkinter front ends.

Puts the project root, bridge/, and installer/ on sys.path once here,
the same bare-import convention the old flat scripts already relied on
(e.g. "import stub_apk", "from console_names import ...", "from
shared.qt_theme import ..."), so every module under bridge/ui/ can use it
without repeating the sys.path dance per file.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_BRIDGE_DIR = _ROOT / "bridge"
_INSTALLER_DIR = _ROOT / "installer"

for _dir in (_ROOT, _BRIDGE_DIR, _INSTALLER_DIR):
    _dir_str = str(_dir)
    if _dir_str not in sys.path:
        sys.path.insert(0, _dir_str)
