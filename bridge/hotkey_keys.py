"""Key and modifier tables for the quit and shutdown hotkeys, and the lookup
from a stored key name to a Windows virtual-key code. Plain data and one pure
function, so the capture UI (bridge/ui/onboarding_wizard.py) and the bridge
(launch_bridge.py) can agree on the stored format and a test can check that
they do.
"""

MODIFIER_FLAGS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}

# Virtual-key codes for GetAsyncKeyState, used by quit_key_watcher below,
# separate from MODIFIER_FLAGS (RegisterHotKey's own bitflags), which
# don't apply here since polling needs each modifier's actual key code.
MODIFIER_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
# Named (non-single-character) keys quit_hotkey's "key" can be set to,
# via the Manager's key-capture UI (lowercase key names), anything not
# listed here falls back to ord(key.upper()[0]), which already covers
# every plain letter/digit key (the only kind this config supported before
# Escape became the default).
NAMED_KEY_VK = {
    "escape": 0x1B, "tab": 0x09, "return": 0x0D, "space": 0x20,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "prior": 0x21, "next": 0x22,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}


def resolve_vk(key: str) -> int:
    return NAMED_KEY_VK.get(key.lower(), ord(key.upper()[0]) if key else 0)
