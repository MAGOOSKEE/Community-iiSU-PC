"""Windows MCI playback for iiDB soundbite previews -- a second, simpler
audio path than winmm_playback.py's WinmmPlayer, used here specifically
because MCI opens compressed formats (mp3, wma, ogg...) natively, unlike
raw waveOut which only understands PCM. iiDB soundbite previews arrive in
whatever format iiDB served them in, whereas Media Library's saved
soundbites are always the PCM WAV MediaBridge installed -- hence two
different players for two different guarantees."""

import ctypes
from pathlib import Path


class MciPlaybackError(Exception):
    pass


_ALIAS = "iisupc_iidb_preview"


def play(path: Path) -> str:
    """Starts playback, closing any previous preview first. Returns the
    alias to pass to stop()."""
    winmm = ctypes.windll.winmm
    winmm.mciSendStringW(f"close {_ALIAS}", None, 0, None)
    err = winmm.mciSendStringW(f'open "{path}" alias {_ALIAS}', None, 0, None)
    if err:
        raise MciPlaybackError(f"Windows audio open failed (MCI {err})")
    err = winmm.mciSendStringW(f"play {_ALIAS}", None, 0, None)
    if err:
        raise MciPlaybackError(f"Windows audio playback failed (MCI {err})")
    return _ALIAS


def stop(alias: str | None) -> None:
    if not alias:
        return
    try:
        winmm = ctypes.windll.winmm
        winmm.mciSendStringW(f"stop {alias}", None, 0, None)
        winmm.mciSendStringW(f"close {alias}", None, 0, None)
    except Exception:
        pass
