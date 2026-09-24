"""Native Windows waveOut (WinMM) playback for short PCM WAV soundbites --
verbatim carry-over of manager.py's ctypes WinMM code, just reorganized
into a plain state-machine class with no GUI-toolkit dependency (no Tk,
no Qt) so bridge/ui/pages/media_library.py can drive it with a QTimer the
same way manager.py drove it with self.after().

Only plays uncompressed PCM WAV -- iiDB soundbites are always this format,
so no other codec support was ever needed here.
"""

from __future__ import annotations

import ctypes
import time
import wave
from ctypes import wintypes
from pathlib import Path


class WinmmPlaybackError(Exception):
    pass


def format_ms(value: int) -> str:
    seconds = max(0, int(value) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def load_wav(path: Path) -> dict:
    """Load a standard PCM WAV for the native Windows waveOut previewer."""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.getnframes()
        comptype = wav.getcomptype()
        if comptype != "NONE":
            raise WinmmPlaybackError(f"Unsupported WAV compression: {comptype}")
        if width not in (1, 2):
            raise WinmmPlaybackError(f"Unsupported WAV sample width: {width * 8}-bit")
        if channels not in (1, 2):
            raise WinmmPlaybackError(f"Unsupported WAV channel count: {channels}")
        pcm = wav.readframes(frames)
    return {
        "path": path,
        "channels": channels,
        "width": width,
        "rate": rate,
        "frames": frames,
        "pcm": pcm,
        "length_ms": int((frames * 1000) / rate) if rate else 0,
        "block_align": channels * width,
    }


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


def _raise_on_error(code: int, operation: str) -> None:
    if code:
        raise WinmmPlaybackError(f"Windows waveOut {operation} failed (MMRESULT {code})")


class WinmmPlayer:
    """One soundbite at a time -- state is one of "stopped"/"playing"/
    "paused". Callers poll position_ms()/state on a timer (e.g. every
    100ms) to drive a seek bar/time label; there is no playback-finished
    callback, matching the original polling-based design."""

    def __init__(self):
        self.state = "stopped"
        self.length_ms = 0
        self._audio: dict | None = None
        self._winmm = None
        self._handle = None
        self._buffer = None
        self._header = None
        self._base_ms = 0
        self._started_at = 0.0

    def load_and_play(self, path: Path) -> None:
        """Stops whatever was playing, loads path fresh, and starts
        playback from the beginning."""
        self.close()
        audio = load_wav(path)
        self._audio = audio
        self.length_ms = audio["length_ms"]
        self._open(audio)
        self._submit_from(0)
        self.state = "playing"

    @property
    def has_audio_loaded(self) -> bool:
        return self._audio is not None

    def toggle(self) -> str:
        """Play/pause/resume/restart. Returns the new state. No-op if
        nothing has ever been loaded (callers should call load_and_play
        first in that case)."""
        if self.state == "playing":
            self.pause()
        elif self.state == "paused":
            self.resume()
        elif self.state == "stopped" and self._audio is not None:
            self.seek(0, autoplay=True)
        return self.state

    def pause(self) -> None:
        _raise_on_error(self._winmm.waveOutPause(self._handle), "pause")
        elapsed = int((time.monotonic() - self._started_at) * 1000)
        self._base_ms = min(self.length_ms, self._base_ms + elapsed)
        self.state = "paused"

    def resume(self) -> None:
        _raise_on_error(self._winmm.waveOutRestart(self._handle), "resume")
        self._started_at = time.monotonic()
        self.state = "playing"

    def seek(self, target_ms: int, autoplay: bool | None = None) -> None:
        if self._audio is None:
            return
        target_ms = max(0, min(self.length_ms, int(target_ms)))
        if autoplay is None:
            autoplay = self.state == "playing"
        self._release_buffer()
        self._submit_from(target_ms)
        if not autoplay:
            _raise_on_error(self._winmm.waveOutPause(self._handle), "pause")
            self.state = "paused"
        else:
            self.state = "playing"

    def position_ms(self) -> int:
        base = self._base_ms
        if self.state == "playing":
            base += int((time.monotonic() - self._started_at) * 1000)
        return max(0, min(self.length_ms, base))

    def mark_ended(self) -> None:
        """Called by the caller's tick once position reaches length_ms --
        resets to the start without tearing down the loaded buffer/handle,
        so toggle() can replay the same soundbite immediately."""
        self.state = "stopped"
        self._base_ms = 0

    def set_volume(self, level_0_1000: int) -> None:
        if not self._handle:
            return
        level = max(0, min(1000, int(level_0_1000)))
        word = int(level * 0xFFFF / 1000)
        packed = (word << 16) | word
        try:
            _raise_on_error(self._winmm.waveOutSetVolume(self._handle, packed), "volume")
        except WinmmPlaybackError:
            pass

    def close(self) -> None:
        """Full teardown -- releases the WinMM handle and forgets the
        loaded audio entirely. Call this when switching to a different
        asset, not when a track merely finishes (see mark_ended)."""
        if self._handle and self._winmm:
            try:
                self._winmm.waveOutReset(self._handle)
                if self._header is not None:
                    self._winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(self._header), ctypes.sizeof(self._header))
                self._winmm.waveOutClose(self._handle)
            except Exception:
                pass
        self._winmm = None
        self._handle = None
        self._buffer = None
        self._header = None
        self._audio = None
        self.state = "stopped"
        self.length_ms = 0
        self._base_ms = 0

    # -- Internals -------------------------------------------------

    def _open(self, audio: dict) -> None:
        winmm = ctypes.WinDLL("winmm")
        fmt = _WAVEFORMATEX()
        fmt.wFormatTag = 1
        fmt.nChannels = audio["channels"]
        fmt.nSamplesPerSec = audio["rate"]
        fmt.wBitsPerSample = audio["width"] * 8
        fmt.nBlockAlign = audio["block_align"]
        fmt.nAvgBytesPerSec = audio["rate"] * audio["block_align"]
        fmt.cbSize = 0

        handle = ctypes.c_void_p()
        result = winmm.waveOutOpen(ctypes.byref(handle), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0)
        _raise_on_error(result, "open")

        self._handle = handle
        self._winmm = winmm

    def _submit_from(self, position_ms: int) -> None:
        audio = self._audio
        frame = max(0, min(audio["frames"], int(position_ms * audio["rate"] / 1000)))
        byte_offset = frame * audio["block_align"]
        chunk = audio["pcm"][byte_offset:]
        if not chunk:
            self.state = "stopped"
            return

        buf = ctypes.create_string_buffer(chunk)
        hdr = _WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = len(chunk)
        hdr.dwBytesRecorded = 0
        hdr.dwUser = 0
        hdr.dwFlags = 0
        hdr.dwLoops = 0
        hdr.lpNext = None
        hdr.reserved = 0

        result = self._winmm.waveOutPrepareHeader(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        _raise_on_error(result, "prepare")
        result = self._winmm.waveOutWrite(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if result:
            self._winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
            _raise_on_error(result, "write")

        self._buffer = buf
        self._header = hdr
        self._base_ms = position_ms
        self._started_at = time.monotonic()

    def _release_buffer(self) -> None:
        if self._handle and self._header is not None and self._winmm:
            self._winmm.waveOutReset(self._handle)
            self._winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(self._header), ctypes.sizeof(self._header))
        self._header = None
        self._buffer = None
