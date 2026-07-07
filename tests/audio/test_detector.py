"""Tests for ``traktorco.audio.detector`` against a deterministic synthetic
fixture, exercising ``traktorco.audio.loader`` for real along the way.

Strictly aligned with ``.openspec/2-spec.md`` section 6: builds a track
with a known, fixed BPM/grid via
``tests/fixtures/generate_synthetic_fixture.py``, which injects one
deliberate, massive energy + timbre jump exactly at a known 16-beat phrase
boundary (a "major" 32-beat boundary), then verifies
``audio.detector.detect_events`` finds a ``drop`` at exactly that
grid-locked timestamp -- and nowhere else.

Design note: per spec section 6.1, step 6, the anchor candidate (n=0)
always becomes an `intro_end` "of last resort" and the last in-window
candidate always becomes `outro_start` "of last resort", *regardless* of
significance, whenever their search windows are non-empty. That is
correct, spec-mandated behavior, not noise -- so this suite does not
assert their absence. Instead, it asserts that only the deliberate jump
qualifies as a `drop`, and that the fallback intro/outro events carry
deterministically low/zero confidence relative to the real jump.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))
import generate_synthetic_fixture as fixture_gen  # noqa: E402

from traktorco.audio.detector import detect_events  # noqa: E402
from traktorco.audio.loader import load_window  # noqa: E402
from traktorco.config import AppConfig  # noqa: E402


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
    def test_finds_exactly_one_drop_at_the_jump_boundary(self, detected_events):
        drops = [e for e in detected_events if e.label == "drop"]
        assert len(drops) == 1

        drop = drops[0]
        assert drop.time_ms == pytest.approx(fixture_gen.JUMP_TIME_MS)
        assert drop.beat_index == fixture_gen.JUMP_BEAT_INDEX
        assert drop.is_major_phrase is True

    def test_drop_confidence_is_large(self, detected_events):
        # The jump is a deliberate, massive energy increase (quiet sine ->
        # loud square wave) plus a large timbre change -- confidence should
        # be far above the is_significant threshold of 1.0 (spec 6.1, step 5).
        drop = next(e for e in detected_events if e.label == "drop")
        assert drop.confidence > 5.0

    def test_no_other_phrase_boundary_is_flagged_as_a_drop(self, detected_events):
        # Every candidate other than the jump sits entirely within one
        # flat region (all-quiet-sine or all-loud-square) on both its
        # before and after windows -- none of them may ever qualify.
        drops = [e for e in detected_events if e.label == "drop"]
        assert all(d.beat_index == fixture_gen.JUMP_BEAT_INDEX for d in drops)

    def test_intro_end_is_the_anchor_with_zero_signal(self, detected_events):
        # The anchor (n=0) has no "before" window, so it is an unscored
        # intro_end "of last resort" (spec 6.1, step 5) -- deterministically
        # zero confidence, since there is nothing to compare it against.
        intro_events = [e for e in detected_events if e.label == "intro_end"]
        assert len(intro_events) == 1
        assert intro_events[0].time_ms == pytest.approx(fixture_gen.GRID_ANCHOR_MS)
        assert intro_events[0].confidence == pytest.approx(0.0)

    def test_outro_start_confidence_is_far_below_the_real_drop(self, detected_events):
        # The final candidate sits entirely within the loud region on both
        # sides (no real change), so even though the algorithm picks it as
        # outro_start "of last resort" (spec 6.1, step 6), its confidence
        # must be nowhere near the genuine jump's -- proving flat audio is
        # not mistaken for the deliberate structural change.
        outro_events = [e for e in detected_events if e.label == "outro_start"]
        drop = next(e for e in detected_events if e.label == "drop")
        assert len(outro_events) == 1
        assert outro_events[0].confidence < drop.confidence / 5

    def test_events_are_chronologically_ordered(self, detected_events):
        times = [e.time_ms for e in detected_events]
        assert times == sorted(times)


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
