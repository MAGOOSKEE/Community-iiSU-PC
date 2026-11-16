"""
Regenerates the Manager screenshots the README and docs/ show
(docs/images/manager-*.png).

Builds the real Manager window against a throwaway config and an invented ROM
library in a temp folder, so the pictures never show anyone's own paths or
games and bridge/config.json is never read or written. Nothing is started: no
Android VM, no adb call, no update check.

Run from the repo root after a change to how the Manager looks:
    python scripts/capture_screenshots.py

--out DIR writes somewhere else (for a quick look while working), and
--only NAME[,NAME] takes just those shots, by file name without .png.
"""

import argparse
import json
import os
import sys
import tempfile
import unittest.mock
from pathlib import Path

# The docs show the dark theme whatever this PC is set to; IIPC_THEME=light
# takes the light ones instead.
os.environ.setdefault("IIPC_THEME", "dark")

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

# Empty stand-ins, so some consoles show their emulator as installed.
EXAMPLE_EMULATORS = ["pcsx2-qt.exe", "Dolphin.exe", "retroarch.exe"]


def wait_until(condition, timeout_ms: int = 8000) -> None:
    """Runs a real event loop until condition() holds. QTest.qWait's own
    loop barely lets a worker thread run, so a page's background scan could
    take many times longer under it than in the app."""
    from PySide6.QtCore import QElapsedTimer, QEventLoop, QTimer

    loop = QEventLoop()
    clock = QElapsedTimer()
    clock.start()
    timer = QTimer()

    def check() -> None:
        if condition() or clock.elapsed() > timeout_ms:
            loop.quit()

    timer.timeout.connect(check)
    timer.start(50)
    loop.exec()
    timer.stop()



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    only = {name.strip() for name in args.only.split(",") if name.strip()}
    shots = [shot for shot in SHOTS if not only or shot[0].removesuffix(".png") in only]
    output_dir = args.out

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

        emulator_dir = tmp_path / "Emulators"
        emulator_dir.mkdir()
        for name in EXAMPLE_EMULATORS:
            (emulator_dir / name).write_bytes(b"")

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "avd_name": "iisuwin", "roms_dir": str(roms), "bridge_port": 7737,
            "search_roots": [str(emulator_dir)],
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

        # The Console page would otherwise list this install's own per-game exceptions,
        # and Home this install's last launch problem.
        sync_library.load_dedupe_exceptions = lambda: set()
        import launch_problems
        launch_problems.LAST_PROBLEM_PATH = tmp_path / ".last_launch_problem.json"


        with unittest.mock.patch.object(iisu_install_record.load, "__defaults__", (record_path,)):
            window = main_window.ManagerWindow()
            window._status_timer.stop()
            window._dirty_timer.stop()
            window.resize(*WINDOW_SIZE)
            # Laid out and painted like a real window, without appearing on the screen.
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            window.show()
            QTest.qWait(PAGE_SETTLE_MS)

            output_dir.mkdir(parents=True, exist_ok=True)
            for file_name, page, sub_page in shots:
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
                    wait_until(lambda: games_page.status_label.text() != "Scanning...")
                if sub_page == "emulators":
                    emulators_page = window.subpages[sub_page]
                    emulators_page.on_shown()  # looks for each emulator on a worker thread
                    wait_until(lambda: emulators_page.overview.rows)
                QTest.qWait(PAGE_SETTLE_MS)  # the page's fade-in has to finish
                window.grab().save(str(output_dir / file_name))
                print(f"wrote {output_dir / file_name}")

            window.close()


if __name__ == "__main__":
    main()
