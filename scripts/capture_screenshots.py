"""
Regenerates the Manager screenshots the README and docs/ show
(docs/images/manager-*.png).

Builds the real Manager window against a throwaway config and an invented ROM
library in a temp folder, so the pictures never show anyone's own paths or
games and bridge/config.json is never read or written. Nothing is started: no
Android VM, no adb call, no update check.

Run from the repo root after a change to how the Manager looks:
    python scripts/capture_screenshots.py
"""

import json
import sys
import tempfile
import unittest.mock
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "docs" / "images"
WINDOW_SIZE = (1100, 720)
PAGE_SETTLE_MS = 600

# Invented titles: placeholders that read as a library without naming real games.
EXAMPLE_LIBRARY = {
    "psx": ["Starlight Racer (USA).chd", "Moonfall Tactics (Disc 1).chd", "Moonfall Tactics (Disc 2).chd", "Moonfall Tactics.m3u"],
    "ps2": ["Harbor Drift (USA).iso", "Clockwork Arena (Europe).iso"],
    "gc": ["Paper Kite Adventure (USA).rvz"],
    "snes": ["Pixel Orchard (USA).sfc", "Tower of Lanterns (Japan).sfc"],
    "gba": ["Pocket Voyager (USA).gba"],
    "n64": ["Skyline Karts (USA).z64"],
}

# (file name, top-level page, sub-page or None)
SHOTS = [
    ("manager-home.png", "home", None),
    ("manager-games.png", "games", "games_console"),
    ("manager-pc-emulators.png", "settings", "emulators"),
    ("manager-display.png", "settings", "settings"),
]


def main() -> None:
    import bridge.ui  # noqa: F401; puts bridge/ and installer/ on sys.path
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import bridge_config
    import sync_library
    import bridge.ui.main_window as main_window
    import bridge.ui.pages.home as home
    from shared import iisu_install_record
    from shared.emulator_defaults import build_emulators_map
    from shared.qt_theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        roms = tmp_path / "ROMs"
        for console, names in EXAMPLE_LIBRARY.items():
            (roms / console).mkdir(parents=True)
            for name in names:
                (roms / console / name).write_bytes(b"")
        (roms / "psx" / "Moonfall Tactics.m3u").write_text(
            "Moonfall Tactics (Disc 1).chd\nMoonfall Tactics (Disc 2).chd\n", encoding="utf-8"
        )

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "avd_name": "iisuwin", "roms_dir": str(roms), "bridge_port": 7737,
            "emulators": build_emulators_map(),
            "display": {"width": 1920, "height": 1080, "density": 240}, "vm_backend": "avd",
        }), encoding="utf-8")
        record_path = tmp_path / ".iisu_installed.json"
        iisu_install_record.save("iiSU-Alpha-7.5.apk", "0.1.6.1", record_path)

        for module in (bridge_config, main_window, home):
            module.CONFIG_PATH = config_path
        # No page gets to start its own adb calls, and the window's own
        # startup checks (updates, repatch, config recovery) stay quiet.
        for name in ("_trigger_page_side_effects", "_run_startup_update_check", "_prompt_repatch_if_needed",
                     "offer_config_recovery"):
            setattr(main_window.ManagerWindow, name, lambda window, *args: None)

        # The Console page would otherwise list this install's own per-game exceptions.
        sync_library.load_dedupe_exceptions = lambda: set()

        with unittest.mock.patch.object(iisu_install_record.load, "__defaults__", (record_path,)):
            window = main_window.ManagerWindow()
            window._status_timer.stop()
            window._dirty_timer.stop()
            window.resize(*WINDOW_SIZE)
            # Laid out and painted like a real window, without appearing on the screen.
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            window.show()
            QTest.qWait(PAGE_SETTLE_MS)

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            for file_name, page, sub_page in SHOTS:
                window.show_page(page)
                if sub_page:
                    window._show_subpage(page, sub_page)
                if page == "home":
                    # What the status poll reports with nothing running, on an
                    # install that has been started before.
                    window.home_page._apply_status((False, False, True))
                    with unittest.mock.patch.object(home.start_iisu_pc, "describe_boot_fingerprint_diff", return_value=[]),                             unittest.mock.patch.object(home.start_iisu_pc, "has_complete_quickboot_snapshot", return_value=True):
                        window.home_page.refresh_resume_reason()
                if page == "games":
                    games_page = window.subpages[sub_page]
                    games_page.on_shown()  # scans the example library on a worker thread
                    for _ in range(50):
                        if games_page.status_label.text() != "Scanning...":
                            break
                        QTest.qWait(100)
                QTest.qWait(PAGE_SETTLE_MS)  # the page's fade-in has to finish
                window.grab().save(str(OUTPUT_DIR / file_name))
                print(f"wrote docs/images/{file_name}")
            window.close()


if __name__ == "__main__":
    main()
