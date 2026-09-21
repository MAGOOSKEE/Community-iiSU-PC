"""
Applies config.json's "display" settings (width/height/density) to the AVD's
actual hardware profile and cold-boots it so the change takes effect.

gpu_mode is deliberately NOT handled here, unlike the others -- it's passed
straight to emulator.exe as a plain -gpu launch flag by start_iisu_pc.py
instead, since this AVD always cold-boots on every normal start regardless
(-no-snapshot, never resumed), so a GPU-backend choice needs no dedicated
"cold-boot to apply" cycle of its own the way an actual hardware-profile
change (resolution) does: it just takes effect on the very next normal
start, like every other config.json setting already does.

This edits hw.lcd.width/height/density directly in the AVD's config.ini
rather than using the live `adb shell wm size` override: `wm size` only
resizes the logical pixel grid within the emulator's existing physical
panel shape, so going from a portrait phone profile to a landscape
resolution just letterboxes instead of giving a clean full-bleed display.
Changing the actual hardware profile requires a cold boot, but gives a
genuinely native resolution with no letterboxing.

Edits the *portable* AVD copy under android-sdk-portable/avd-home/ (see
portable_sdk.py) -- that's the one every real launch actually uses since
the portable-SDK migration, not ~/.android/avd/ (only ever a one-time copy
source). Restarts everything via stop_iisu_pc.main() + start_iisu_pc.main()
-- the same stop/start the control panel's own buttons use -- rather than
the `android emulator start/stop` CLI wrapper, which start_iisu_pc.py's own
docstring documents as hanging indefinitely once the AVD is actually up.
start_iisu_pc.main() also starts the launch bridge and brings iiSU to the
foreground itself (see launch_bridge.py's launch_iisu()), so this doesn't
need to duplicate that -- without it, display changes would leave the AVD
freshly booted but with no bridge running to redirect game launches.

Run this after changing display settings in manager.py's Display page.
"""

import sys
from pathlib import Path

import stop_iisu_pc
import start_iisu_pc
from bridge_config import ConfigMissingError, load_config
from portable_sdk import PORTABLE_AVD_HOME


def avd_config_path(avd_name: str) -> Path:
    return PORTABLE_AVD_HOME / f"{avd_name}.avd" / "config.ini"


def update_config_ini(path: Path, display: dict) -> None:
    width = display["width"]
    height = display["height"]
    density = display["density"]
    orientation = "landscape" if width >= height else "portrait"

    updates = {
        "hw.lcd.width": str(width),
        "hw.lcd.height": str(height),
        "hw.lcd.density": str(density),
        "hw.lcd.vsync": str(display.get("refresh_rate", 60)),
        "hw.initialOrientation": orientation,
        # A clean full-bleed display reads better fullscreen than a phone
        # bezel graphic around a small screen.
        "showDeviceFrame": "no",
    }

    lines = path.read_text(encoding="utf-8").splitlines()
    seen = set()
    new_lines = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else None
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> None:
    try:
        config = load_config()
    except ConfigMissingError as e:
        print(e)
        sys.exit(1)
    avd_name = config["avd_name"]
    display = config["display"]

    config_ini = avd_config_path(avd_name)
    if not config_ini.is_file():
        print(f"Could not find AVD config at {config_ini}")
        sys.exit(1)

    print(
        f"Updating {config_ini} to {display['width']}x{display['height']} "
        f"@ {display['density']}dpi, {display.get('refresh_rate', 60)}Hz"
    )
    update_config_ini(config_ini, display)

    print(f"Stopping {avd_name} and the bridge (if running)...")
    stop_iisu_pc.main()

    print(f"Cold-booting {avd_name} with the new display profile and restarting the bridge...")
    start_iisu_pc.main()


if __name__ == "__main__":
    main()
