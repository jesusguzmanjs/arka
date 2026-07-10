"""Tests for the v2.2 Multi-Source Validation energy probe added to
``cuegrid.audio.loader``.

Covers ``measure_audio_energy``/``is_drum_stem_empty`` (spec section
10.1): the lightning-fast, seek-based RMS estimate used to detect a
practically silent/ambient drum stem so ``core.pipeline`` can fall back
to the original Master audio instead of analyzing silence.
"""

from __future__ import annotations

import numpy as np
import soundfile as sf
from cuegrid.audio.loader import (
    DRUM_STEM_SILENCE_RMS_THRESHOLD,
    is_drum_stem_empty,
    measure_audio_energy,
)

SAMPLE_RATE = 22_050


def _write_wav(path, y: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    sf.write(str(path), y.astype(np.float32), sr, subtype="PCM_16")


class TestMeasureAudioEnergy:
    def test_silent_file_has_near_zero_energy(self, tmp_path):
        path = tmp_path / "silent.wav"
        y = np.zeros(SAMPLE_RATE * 5, dtype=np.float32)  # 5s of digital silence
        _write_wav(path, y)

        energy = measure_audio_energy(path)

        assert energy < 1e-3

    def test_loud_file_has_high_energy(self, tmp_path):
        path = tmp_path / "loud.wav"
        t = np.arange(SAMPLE_RATE * 5) / SAMPLE_RATE
        y = 0.8 * np.sign(np.sin(2.0 * np.pi * 220.0 * t))  # loud square wave
        _write_wav(path, y)

        energy = measure_audio_energy(path)

        assert energy > 0.5

    def test_short_file_shorter_than_one_chunk_does_not_crash(self, tmp_path):
        path = tmp_path / "tiny.wav"
        y = 0.5 * np.ones(200, dtype=np.float32)  # much shorter than one chunk
        _write_wav(path, y)

        energy = measure_audio_energy(path)

        assert energy > 0.0

    def test_empty_file_returns_zero(self, tmp_path):
        path = tmp_path / "empty.wav"
        _write_wav(path, np.zeros(0, dtype=np.float32))

        assert measure_audio_energy(path) == 0.0


class TestIsDrumStemEmpty:
    def test_true_for_silent_stem(self, tmp_path):
        path = tmp_path / "silent.wav"
        _write_wav(path, np.zeros(SAMPLE_RATE * 5, dtype=np.float32))

        assert is_drum_stem_empty(path) is True

    def test_false_for_loud_stem(self, tmp_path):
        path = tmp_path / "loud.wav"
        t = np.arange(SAMPLE_RATE * 5) / SAMPLE_RATE
        y = 0.8 * np.sign(np.sin(2.0 * np.pi * 220.0 * t))
        _write_wav(path, y)

        assert is_drum_stem_empty(path) is False

    def test_respects_custom_threshold(self, tmp_path):
        path = tmp_path / "quiet.wav"
        t = np.arange(SAMPLE_RATE * 5) / SAMPLE_RATE
        y = 0.05 * np.sin(2.0 * np.pi * 220.0 * t)  # quiet, but not silent
        _write_wav(path, y)

        # Above the module default threshold...
        assert is_drum_stem_empty(path, threshold=0.001) is False
        # ...but below a much stricter custom threshold.
        assert is_drum_stem_empty(path, threshold=1.0) is True

    def test_default_threshold_constant_is_positive(self):
        assert DRUM_STEM_SILENCE_RMS_THRESHOLD > 0.0
