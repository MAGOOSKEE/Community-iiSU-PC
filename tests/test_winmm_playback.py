"""Unit tests for bridge/ui/audio/winmm_playback.py's non-Windows-API logic
(WAV loading/validation, time formatting). WinmmPlayer's actual waveOut
calls need Windows audio hardware and are exercised manually."""

import sys
import tempfile
import unittest
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bridge.ui.audio import winmm_playback as wp


def _write_wav(path: Path, channels: int = 1, width: int = 2, rate: int = 44100, frames: int = 4410) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x00" * frames * channels * width)


class FormatMsTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(wp.format_ms(0), "0:00")

    def test_under_a_minute(self):
        self.assertEqual(wp.format_ms(45_000), "0:45")

    def test_over_a_minute(self):
        self.assertEqual(wp.format_ms(125_000), "2:05")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(wp.format_ms(-500), "0:00")


class LoadWavTests(unittest.TestCase):
    def test_loads_valid_pcm_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            _write_wav(path, channels=1, width=2, rate=44100, frames=44100)
            audio = wp.load_wav(path)
            self.assertEqual(audio["channels"], 1)
            self.assertEqual(audio["width"], 2)
            self.assertEqual(audio["rate"], 44100)
            self.assertEqual(audio["length_ms"], 1000)
            self.assertEqual(audio["block_align"], 2)

    def test_stereo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stereo.wav"
            _write_wav(path, channels=2, width=2, rate=22050, frames=22050)
            audio = wp.load_wav(path)
            self.assertEqual(audio["channels"], 2)
            self.assertEqual(audio["block_align"], 4)
            self.assertEqual(audio["length_ms"], 1000)


class WinmmPlayerStateTests(unittest.TestCase):
    """Exercises the state machine without touching the real WinMM API by
    stubbing the internal open/submit/release methods."""

    def _make_player_with_fake_audio(self, length_ms=5000):
        player = wp.WinmmPlayer()
        player._audio = {"frames": 1000, "rate": 200, "pcm": b"\x00" * 2000, "block_align": 2}
        player.length_ms = length_ms
        player._winmm = None
        player._handle = "fake-handle"

        # Stub out the real WinMM calls so state transitions can be tested headlessly.
        player._open = lambda audio: None
        player._submit_from = lambda pos: setattr(player, "_base_ms", pos)
        player._release_buffer = lambda: None
        player.pause = lambda: setattr(player, "state", "paused")
        player.resume = lambda: setattr(player, "state", "playing")
        return player

    def test_toggle_from_stopped_with_no_audio_is_a_noop(self):
        player = wp.WinmmPlayer()
        self.assertEqual(player.toggle(), "stopped")

    def test_toggle_from_stopped_with_loaded_audio_seeks_to_zero_and_plays(self):
        player = self._make_player_with_fake_audio()
        self.assertEqual(player.toggle(), "playing")
        self.assertEqual(player._base_ms, 0)

    def test_toggle_playing_then_paused_then_playing(self):
        player = self._make_player_with_fake_audio()
        player.state = "playing"
        self.assertEqual(player.toggle(), "paused")
        self.assertEqual(player.toggle(), "playing")

    def test_mark_ended_resets_without_clearing_loaded_audio(self):
        player = self._make_player_with_fake_audio()
        player.state = "playing"
        player.mark_ended()
        self.assertEqual(player.state, "stopped")
        self.assertEqual(player._base_ms, 0)
        self.assertIsNotNone(player._audio)

    def test_close_clears_everything(self):
        player = self._make_player_with_fake_audio()
        player.close()
        self.assertEqual(player.state, "stopped")
        self.assertIsNone(player._audio)
        self.assertEqual(player.length_ms, 0)


if __name__ == "__main__":
    unittest.main()
