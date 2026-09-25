"""
Quick manual test: shows the new Qt boot overlay for 5 seconds, then closes
it automatically. Safe to run, doesn't touch any real setup/AVD logic,
just displays the overlay window. Delete this file once you're done
checking it out; it's not part of the app, just a throwaway test script.

Run with (from any directory, that used to matter, see boot_overlay_qt.py):
    python try_boot_overlay.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bridge.ui import boot_overlay_qt as boot_overlay

print("Showing the boot overlay for 5 seconds...")
overlay = boot_overlay.show("Booting Community-iiSU-PC...")
if overlay is None:
    print("Couldn't start the overlay process, see any error above.")
elif overlay.poll() is not None:
    print(f"It exited immediately with code {overlay.returncode} instead of showing, something's still wrong.")
else:
    time.sleep(5)
    boot_overlay.close(overlay)
    print("Closed.")
