"""Tests for ``traktorco.audio.detector`` against deterministic synthetic
fixtures, exercising ``traktorco.audio.loader`` for real along the way.

Strictly aligned with ``.openspec/2-spec.md`` section 6 (v1.4): builds a
track with a known, fixed BPM/grid via
``tests/fixtures/generate_synthetic_fixture.py``, which injects one
deliberate, massive energy + timbre jump exactly at a known 16-beat phrase
boundary (a "major" 32-beat boundary), then verifies
``audio.detector.detect_events`` finds a single unified ``cue`` at exactly
that grid-locked timestamp -- and nowhere else.

Design note: per spec section 6.1, step 6 (v1.4), there are no more
position-based intro/drop/outro roles -- every significant candidate
competes in a single pool, filtered by a dynamic confidence threshold and
capped at ``config.max_cues``. The synthetic fixture only has one
significant candidate (the deliberate jump), so it produces a single
``cue`` event -- correct, spec-mandated behavior.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tests.fixtures import generate_synthetic_fixture as fixture_gen
from traktorco.audio.detector import detect_events
from traktorco.audio.loader import load_window
from traktorco.config import AppConfig


@pytest.fixture(scope="module")
def synthetic_track_path() -> Path:
    """(Re)generate the deterministic synthetic fixture at its canonical
    path, ``tests/fixtures/sample_track.wav``.

    Regenerated at test time from ``generate_synthetic_fixture.py``'s
    constants (rather than trusting a possibly-stale committed binary),
    so the test and the fixture can never drift apart. The file is not
    checked into version control (see ``.gitignore``) since it is fully,
    deterministically reproducible from the generator script.
    """
    return fixture_gen.generate()


@pytest.fixture(scope="module")
def detected_events(synthetic_track_path: Path) -> list:
    config = (
        AppConfig()
    )  # spec defaults -- the fixture's jump is designed to clear them
    return detect_events(
        audio_path=synthetic_track_path,
        bpm=fixture_gen.BPM,
        grid_anchor_ms=fixture_gen.GRID_ANCHOR_MS,
        duration_ms=fixture_gen.DURATION_MS,
        config=config,
    )


class TestDetectEventsOnSyntheticJump:
    def test_finds_exactly_one_cue_at_the_jump_boundary(self, detected_events):
        assert len(detected_events) == 1

        cue = detected_events[0]
        assert cue.label == "cue"
        assert cue.time_ms == pytest.approx(fixture_gen.JUMP_TIME_MS)
        assert cue.beat_index == fixture_gen.JUMP_BEAT_INDEX
        assert cue.is_major_phrase is True

    def test_cue_confidence_is_large(self, detected_events):
        # The jump is a deliberate, massive energy increase (quiet sine ->
        # loud square wave) plus a large timbre change -- confidence should
        # be far above the is_significant threshold of 1.0 (spec 6.1, step 5).
        cue = detected_events[0]
        assert cue.confidence > 5.0

    def test_no_other_phrase_boundary_is_flagged(self, detected_events):
        # Every candidate other than the jump sits entirely within one
        # flat region (all-quiet-sine or all-loud-square) on both its
        # before and after windows -- none of them may ever qualify.
        assert all(e.beat_index == fixture_gen.JUMP_BEAT_INDEX for e in detected_events)

    def test_events_are_chronologically_ordered(self, detected_events):
        times = [e.time_ms for e in detected_events]
        assert times == sorted(times)


class TestUnifiedCuePoolSelection:
    """Exercises spec section 6.1, step 6 (v1.4): the dynamic
    relative-confidence threshold and ``max_cues`` cap over a single,
    unified pool of candidates -- built with a dedicated multi-jump
    fixture so several genuinely distinct confidence levels exist.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def multi_jump_track(tmp_path_factory) -> Path:
        """A track with three energy step-ups of increasing size, each
        exactly on a phrase boundary, so several distinct confidence
        levels exist to filter/rank against each other.

        Layout (BPM=120, phrase_beats=16 -> 8000ms per candidate):
        - [0, 8000)ms: quiet sine (baseline)
        - [8000, 16000)ms: medium square wave (small step -> weak candidate)
        - [16000, 24000)ms: loud square wave (bigger step -> stronger candidate)
        - [24000, 32000)ms: very loud, higher-frequency square wave (biggest
          step -> strongest candidate)
        """
        sample_rate = fixture_gen.SAMPLE_RATE
        duration_ms = 32_000.0
        n_samples = int(round(duration_ms / 1000.0 * sample_rate))
        t = np.arange(n_samples) / sample_rate

        y = np.zeros(n_samples, dtype=np.float32)
        segments_ms = [0, 8000, 16000, 24000, 32000]
        amplitudes = [0.02, 0.15, 0.5, 0.9]
        freqs = [220.0, 440.0, 660.0, 990.0]
        for i in range(4):
            start_sample = int(round(segments_ms[i] / 1000.0 * sample_rate))
            end_sample = int(round(segments_ms[i + 1] / 1000.0 * sample_rate))
            if i == 0:
                seg = amplitudes[i] * np.sin(
                    2.0 * np.pi * freqs[i] * t[start_sample:end_sample]
                )
            else:
                seg = amplitudes[i] * np.sign(
                    np.sin(2.0 * np.pi * freqs[i] * t[start_sample:end_sample])
                )
            y[start_sample:end_sample] = seg.astype(np.float32)

        path = tmp_path_factory.mktemp("multi_jump") / "multi_jump.wav"
        sf.write(str(path), y, sample_rate, subtype="PCM_16")
        return path

    def test_dynamic_threshold_drops_weak_candidates(self, multi_jump_track):
        # A high relative_confidence_threshold should only keep the
        # strongest candidate(s), discarding the weak first step-up.
        config = AppConfig(relative_confidence_threshold=0.9, max_cues=8)
        events = detect_events(
            audio_path=multi_jump_track,
            bpm=fixture_gen.BPM,
            grid_anchor_ms=0.0,
            duration_ms=32_000.0,
            config=config,
        )
        assert len(events) >= 1
        max_confidence = max(e.confidence for e in events)
        for e in events:
            assert e.confidence >= max_confidence * 0.9

    def test_low_threshold_keeps_more_candidates(self, multi_jump_track):
        strict_config = AppConfig(relative_confidence_threshold=0.9, max_cues=8)
        lenient_config = AppConfig(relative_confidence_threshold=0.01, max_cues=8)

        strict_events = detect_events(
            audio_path=multi_jump_track,
            bpm=fixture_gen.BPM,
            grid_anchor_ms=0.0,
            duration_ms=32_000.0,
            config=strict_config,
        )
        lenient_events = detect_events(
            audio_path=multi_jump_track,
            bpm=fixture_gen.BPM,
            grid_anchor_ms=0.0,
            duration_ms=32_000.0,
            config=lenient_config,
        )
        assert len(lenient_events) >= len(strict_events)

    def test_max_cues_caps_the_result(self, multi_jump_track):
        config = AppConfig(relative_confidence_threshold=0.0, max_cues=1)
        events = detect_events(
            audio_path=multi_jump_track,
            bpm=fixture_gen.BPM,
            grid_anchor_ms=0.0,
            duration_ms=32_000.0,
            config=config,
        )
        assert len(events) <= 1

    def test_events_remain_chronologically_ordered_after_ranking(
        self, multi_jump_track
    ):
        config = AppConfig(relative_confidence_threshold=0.0, max_cues=8)
        events = detect_events(
            audio_path=multi_jump_track,
            bpm=fixture_gen.BPM,
            grid_anchor_ms=0.0,
            duration_ms=32_000.0,
            config=config,
        )
        times = [e.time_ms for e in events]
        assert times == sorted(times)


class TestAntiSilenceFilter:
    """Exercises spec section 6.1, step 5 (v1.4): a candidate must never be
    confirmed as significant if its "after" window is practically silent,
    even though a quiet-to-near-silent transition can otherwise produce a
    large (negative) ``energy_delta_db``.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def fade_out_track(tmp_path_factory) -> Path:
        """A track that is loud for its first phrase, then fades all the
        way down to near-total silence for its second phrase -- the kind
        of ending that must never produce a spurious cue.

        Layout (BPM=120, phrase_beats=16 -> 8000ms per candidate):
        - [0, 8000)ms: loud square wave
        - [8000, 16000)ms: practically silent (near-zero amplitude)
        """
        sample_rate = fixture_gen.SAMPLE_RATE
        duration_ms = 16_000.0
        n_samples = int(round(duration_ms / 1000.0 * sample_rate))
        t = np.arange(n_samples) / sample_rate

        loud = 0.8 * np.sign(np.sin(2.0 * np.pi * 440.0 * t))
        silent = 1e-6 * np.sin(2.0 * np.pi * 440.0 * t)

        jump_sample = int(round(8000.0 / 1000.0 * sample_rate))
        y = loud.astype(np.float32)
        y[jump_sample:] = silent[jump_sample:].astype(np.float32)

        path = tmp_path_factory.mktemp("fade_out") / "fade_out.wav"
        sf.write(str(path), y, sample_rate, subtype="PCM_16")
        return path

    def test_fade_out_boundary_never_becomes_a_cue(self, fade_out_track):
        # Without the anti-silence filter this candidate would have a huge
        # |energy_delta_db| (loud -> near-silent) and thus a huge, spurious
        # confidence score. The filter must reject it outright.
        config = AppConfig(relative_confidence_threshold=0.0, max_cues=8)
        events = detect_events(
            audio_path=fade_out_track,
            bpm=fixture_gen.BPM,
            grid_anchor_ms=0.0,
            duration_ms=16_000.0,
            config=config,
        )
        assert events == []


class TestLoaderIntegration:
    """Light integration coverage proving audio.loader (spec section 2.1)
    is genuinely decoding distinguishable windows around the jump --
    complementary to, not a replacement for, the detector-level
    assertions above.
    """

    def test_before_and_after_windows_differ_across_the_jump(
        self, synthetic_track_path
    ):
        window_sec = 1.0
        jump_sec = fixture_gen.JUMP_TIME_MS / 1000.0

        y_before, sr_before = load_window(
            synthetic_track_path,
            offset_sec=jump_sec - window_sec,
            duration_sec=window_sec,
        )
        y_after, sr_after = load_window(
            synthetic_track_path, offset_sec=jump_sec, duration_sec=window_sec
        )

        assert sr_before == sr_after == fixture_gen.SAMPLE_RATE
        rms_before = (y_before**2).mean() ** 0.5
        rms_after = (y_after**2).mean() ** 0.5
        assert rms_after > rms_before * 5  # loud square wave vs. quiet sine

    def test_load_window_rejects_negative_offset(self, synthetic_track_path):
        with pytest.raises(ValueError):
            load_window(synthetic_track_path, offset_sec=-1.0, duration_sec=1.0)

    def test_load_window_rejects_non_positive_duration(self, synthetic_track_path):
        with pytest.raises(ValueError):
            load_window(synthetic_track_path, offset_sec=0.0, duration_sec=0.0)
