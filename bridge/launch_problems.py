"""Launch failures in plain words, and the last one kept for the Manager.

The bridge's launch log records what happened in its own terms ("FAILED:
none of ['pcsx2-qt.exe'] found under [...]"), which is right for a log and
wrong for someone holding a controller in front of a TV. Each failure worth
telling someone about also gets a sentence that says what went wrong and
where to fix it, shown on screen over iiSU (see boot_overlay_qt.show_problem)
and saved here so the Manager's Home page can show it again later, after the
on-screen card is long gone.

Kept free of Qt and of launch_bridge's own state so it can be tested alone.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

LAST_PROBLEM_PATH = Path(__file__).resolve().parent / ".last_launch_problem.json"

EMULATORS_PAGE = "the Manager's Settings > PC Emulators"
PC_GAMES_PAGE = "the Manager's Games > PC"


def emulator_missing(emulator: str) -> str:
    return (
        f"{emulator} isn't installed, or isn't in one of your emulator folders. "
        f"Install it from {EMULATORS_PAGE}, then try the game again."
    )


def no_emulator_for_console() -> str:
    return f"No PC emulator is set for this console. Pick one in {EMULATORS_PAGE}."


def rom_missing(rom: str) -> str:
    return (
        f"\"{rom}\" isn't in your ROM folder any more. If you moved or renamed it, "
        "stop and start iiSU so its library catches up."
    )


def emulator_exited(emulator: str, code: int) -> str:
    # Windows reports -1 as 4294967295; show the number the program meant.
    if code is not None and code >= 2**31:
        code -= 2**32
    return (

        f"{emulator} closed with an error (code {code}). If the game didn't start, "
        f"open it in {emulator} directly to see why: often a missing BIOS or setting."
    )


def not_runnable(program: str) -> str:
    return f"{program} isn't allowed to run. Mark it as a program (Properties > Permissions, or chmod +x) and try again."


def pc_game_missing(app_name: str) -> str:
    return f"\"{app_name}\" isn't where it was set up to be. Fix or remove it in {PC_GAMES_PAGE}."


def pc_game_misconfigured(app_name: str) -> str:
    return f"\"{app_name}\" isn't set up completely. Edit it in {PC_GAMES_PAGE}."


def pc_game_not_registered(app_name: str) -> str:
    return f"\"{app_name}\" isn't in your PC games list any more. Add it again in {PC_GAMES_PAGE}."


def pc_game_no_window(app_name: str) -> str:
    return f"\"{app_name}\" started, but no window appeared. It may still be loading, or it closed straight away."


def pc_game_failed(app_name: str) -> str:
    return f"\"{app_name}\" couldn't be started. Check it in {PC_GAMES_PAGE}."


def already_running() -> str:
    return "A game is already running. Quit it first (Escape, or Select+Start on a controller)."


def unexpected() -> str:
    return "Something went wrong opening that game. The details are in the Manager's Backup & Tools > Diagnostics."


def record(title: str, message: str, detail: str, path: Path | None = None, now: float | None = None) -> None:
    """Keeps this as the last problem, for the Home page. Best effort: a
    file that can't be written never gets in the way of the launch."""
    path = path or LAST_PROBLEM_PATH
    data = {"time": now if now is not None else time.time(), "title": title, "message": message, "detail": detail}
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def load(path: Path | None = None) -> dict | None:
    """The last problem not yet dismissed, or None."""
    path = path or LAST_PROBLEM_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("dismissed") or not data.get("message"):
        return None
    return data


def dismiss(path: Path | None = None) -> None:
    path = path or LAST_PROBLEM_PATH
    data = load(path)
    if data is None:
        return
    data["dismissed"] = True
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def describe_time(timestamp: float, now: float | None = None) -> str:
    """"just now", "12 minutes ago", "at 14:05", "on 3 Nov"."""
    now = now if now is not None else time.time()
    seconds = max(0, now - timestamp)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if time.localtime(timestamp)[:3] == time.localtime(now)[:3]:
        return "at " + time.strftime("%H:%M", time.localtime(timestamp))
    return "on " + time.strftime("%d %b", time.localtime(timestamp)).lstrip("0")
