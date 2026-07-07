"""Tests for ``traktorco.audio.beatgrid``.

Covers the formula and edge cases from ``.openspec/2-spec.md`` section 4
(Grid-Guided Phrase Candidate Generation).
"""

import pytest

from traktorco.audio.beatgrid import beat_length_ms, generate_phrase_candidates


class TestBeatLengthMs:
    def test_128_bpm(self):
        assert beat_length_ms(128.0) == pytest.approx(468.75)

    def test_174_bpm(self):
        assert beat_length_ms(174.0) == pytest.approx(344.827586, rel=1e-6)

    def test_120_bpm_round_number(self):
        # 120 BPM -> exactly 500ms/beat, a convenient sanity check.
        assert beat_length_ms(120.0) == pytest.approx(500.0)

    @pytest.mark.parametrize("bpm", [0.0, -1.0, -128.0])
    def test_raises_on_non_positive_bpm(self, bpm):
        with pytest.raises(ValueError):
            beat_length_ms(bpm)


class TestGeneratePhraseCandidates:
    def test_first_candidate_is_the_grid_anchor(self):
        candidates = generate_phrase_candidates(
            bpm=120.0, grid_anchor_ms=200.0, duration_ms=100_000.0
        )
        assert candidates[0].beat_index == 0
        assert candidates[0].time_ms == pytest.approx(200.0)
        assert candidates[0].is_major_phrase is True

    def test_candidates_spaced_by_phrase_beats(self):
        bpm = 120.0  # 500ms/beat
        candidates = generate_phrase_candidates(
            bpm=bpm, grid_anchor_ms=0.0, duration_ms=100_000.0, phrase_beats=16
        )
        # 16 beats * 500ms = 8000ms between consecutive candidates.
        for i in range(1, len(candidates)):
            spacing_ms = candidates[i].time_ms - candidates[i - 1].time_ms
            assert spacing_ms == pytest.approx(8000.0)
            assert candidates[i].beat_index - candidates[i - 1].beat_index == 16

    def test_every_other_candidate_is_major_phrase_with_default_multiple(self):
        candidates = generate_phrase_candidates(
            bpm=120.0, grid_anchor_ms=0.0, duration_ms=100_000.0, phrase_beats=16
        )
        # major_phrase_multiple defaults to 2 -> every 32 beats (n even).
        for candidate in candidates:
            expected_major = (candidate.beat_index // 16) % 2 == 0
            assert candidate.is_major_phrase == expected_major

    def test_last_candidate_does_not_exceed_duration(self):
        bpm = 120.0  # 500ms/beat, 16 beats = 8000ms/candidate
        duration_ms = 20_500.0
        candidates = generate_phrase_candidates(
            bpm=bpm, grid_anchor_ms=0.0, duration_ms=duration_ms, phrase_beats=16
        )
        assert all(c.time_ms <= duration_ms for c in candidates)
        # Candidates land at 0, 8000, 16000 -- the next (24000) exceeds 20500.
        assert [c.time_ms for c in candidates] == pytest.approx([0.0, 8000.0, 16000.0])

    def test_grid_anchor_offset_is_preserved(self):
        bpm = 120.0
        grid_anchor_ms = 356.0
        candidates = generate_phrase_candidates(
            bpm=bpm,
            grid_anchor_ms=grid_anchor_ms,
            duration_ms=20_000.0,
            phrase_beats=16,
        )
        length_ms = beat_length_ms(bpm)
        for candidate in candidates:
            expected = grid_anchor_ms + candidate.beat_index * length_ms
            assert candidate.time_ms == pytest.approx(expected)

    def test_custom_phrase_beats_and_major_multiple(self):
        # phrase_beats=8, major_phrase_multiple=4 -> major every 32 beats still,
        # but candidates every 8 beats in between.
        bpm = 120.0
        candidates = generate_phrase_candidates(
            bpm=bpm,
            grid_anchor_ms=0.0,
            duration_ms=50_000.0,
            phrase_beats=8,
            major_phrase_multiple=4,
        )
        beat_indices = [c.beat_index for c in candidates]
        assert beat_indices == sorted(beat_indices)
        assert beat_indices[1] - beat_indices[0] == 8
        for candidate in candidates:
            expected_major = (candidate.beat_index // 8) % 4 == 0
            assert candidate.is_major_phrase == expected_major

    def test_no_candidates_before_anchor(self):
        # Unlike v1's corrective snapping, generation starts at n=0 (the
        # anchor itself) -- there is nothing before it to consider.
        candidates = generate_phrase_candidates(
            bpm=120.0, grid_anchor_ms=1000.0, duration_ms=20_000.0
        )
        assert all(c.time_ms >= 1000.0 for c in candidates)

    @pytest.mark.parametrize("bpm", [0.0, -1.0, -128.0])
    def test_empty_list_on_non_positive_bpm(self, bpm):
        # Section 4.4 item 2: skip generation entirely, never divide by zero.
        assert (
            generate_phrase_candidates(
                bpm=bpm, grid_anchor_ms=0.0, duration_ms=100_000.0
            )
            == []
        )

    @pytest.mark.parametrize("duration_ms", [0.0, -1.0, -5000.0])
    def test_empty_list_on_non_positive_duration(self, duration_ms):
        # Section 4.4 item 3: malformed/zero duration produces no candidates.
        assert (
            generate_phrase_candidates(
                bpm=128.0, grid_anchor_ms=0.0, duration_ms=duration_ms
            )
            == []
        )

    def test_single_candidate_when_duration_barely_covers_anchor(self):
        # Duration equal to the anchor itself should still yield exactly
        # one candidate (the anchor), per spec section 4.4 item 3.
        candidates = generate_phrase_candidates(
            bpm=120.0, grid_anchor_ms=500.0, duration_ms=500.0
        )
        assert len(candidates) == 1
        assert candidates[0].beat_index == 0
        assert candidates[0].time_ms == pytest.approx(500.0)

    def test_closed_form_matches_stepwise_reference(self):
        # Cross-check against the closed-form expression from spec
        # section 4.3 directly, for a non-trivial BPM/anchor combination.
        bpm = 174.0
        grid_anchor_ms = 128.4
        duration_ms = 240_000.0
        phrase_beats = 16

        length_ms = 60_000.0 / bpm
        expected_times = []
        n = 0
        while True:
            t_ms = grid_anchor_ms + n * phrase_beats * length_ms
            if t_ms > duration_ms:
                break
            expected_times.append(t_ms)
            n += 1

        candidates = generate_phrase_candidates(
            bpm=bpm,
            grid_anchor_ms=grid_anchor_ms,
            duration_ms=duration_ms,
            phrase_beats=phrase_beats,
        )
        assert [c.time_ms for c in candidates] == pytest.approx(expected_times)
