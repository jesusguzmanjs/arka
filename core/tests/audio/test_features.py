"""Tests for ``cuegrid.audio.features``.

Covers the scoring formulas from ``.openspec/2-spec.md`` section 6.1,
step 5. No audio decoding is needed -- these are pure functions of
scalar/vector inputs.
"""

import numpy as np
import pytest

from cuegrid.audio import (
    confidence_score,
    energy_delta_db,
    is_significant_change,
    score_candidate,
    timbre_distance,
)
from cuegrid.config import AppConfig


class TestEnergyDeltaDb:
    def test_no_change_is_zero_db(self):
        assert energy_delta_db(rms_before=0.1, rms_after=0.1) == pytest.approx(0.0)

    def test_doubling_energy_is_positive(self):
        # 20*log10(2) ~= 6.02 dB
        assert energy_delta_db(rms_before=0.1, rms_after=0.2) == pytest.approx(
            6.0206, rel=1e-4
        )

    def test_halving_energy_is_negative(self):
        assert energy_delta_db(rms_before=0.2, rms_after=0.1) == pytest.approx(
            -6.0206, rel=1e-4
        )

    def test_silent_before_window_does_not_raise(self):
        # Must not divide by zero / log(0) on a fully silent "before" window.
        result = energy_delta_db(rms_before=0.0, rms_after=0.1)
        assert result > 0
        assert np.isfinite(result)

    def test_silent_after_window_does_not_raise(self):
        result = energy_delta_db(rms_before=0.1, rms_after=0.0)
        assert result < 0
        assert np.isfinite(result)

    def test_both_silent_is_zero_db(self):
        assert energy_delta_db(rms_before=0.0, rms_after=0.0) == pytest.approx(0.0)


class TestTimbreDistance:
    def test_identical_vectors_have_zero_distance(self):
        vec = np.array([1.0, 2.0, 3.0])
        assert timbre_distance(vec, vec) == pytest.approx(0.0)

    def test_known_euclidean_distance(self):
        before = np.array([0.0, 0.0, 0.0])
        after = np.array([3.0, 4.0, 0.0])
        assert timbre_distance(before, after) == pytest.approx(5.0)

    def test_accepts_plain_lists(self):
        assert timbre_distance([0.0, 0.0], [3.0, 4.0]) == pytest.approx(5.0)


class TestConfidenceScore:
    def test_at_threshold_energy_only_gives_confidence_one(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert confidence_score(3.0, 0.0, config) == pytest.approx(1.0)

    def test_at_threshold_timbre_only_gives_confidence_one(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert confidence_score(0.0, 12.0, config) == pytest.approx(1.0)

    def test_combines_both_signals_additively(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert confidence_score(6.0, 6.0, config) == pytest.approx(2.0 + 0.5)

    def test_uses_absolute_value_of_energy_delta(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert confidence_score(-6.0, 0.0, config) == confidence_score(6.0, 0.0, config)


class TestIsSignificantChange:
    def test_below_both_thresholds_is_not_significant(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert is_significant_change(1.0, 5.0, config) is False

    def test_energy_alone_can_trigger_significance(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert is_significant_change(3.5, 0.0, config) is True

    def test_timbre_alone_can_trigger_significance(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert is_significant_change(0.0, 12.5, config) is True

    def test_exactly_at_threshold_counts_as_significant(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert is_significant_change(3.0, 0.0, config) is True
        assert is_significant_change(0.0, 12.0, config) is True

    def test_negative_energy_delta_uses_absolute_value(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        assert is_significant_change(-3.5, 0.0, config) is True


class TestScoreCandidate:
    def test_combines_all_sub_scores_consistently(self):
        config = AppConfig(
            energy_change_threshold_db=3.0, timbre_change_distance_threshold=12.0
        )
        harmonic_before, harmonic_after = 0.1, 0.1
        percussive_before, percussive_after = 0.1, 0.2
        mfcc_before = np.array([0.0, 0.0])
        mfcc_after = np.array([3.0, 4.0])

        result = score_candidate(
            harmonic_before,
            harmonic_after,
            percussive_before,
            percussive_after,
            mfcc_before,
            mfcc_after,
            config,
        )

        expected_harmonic_delta_db = energy_delta_db(harmonic_before, harmonic_after)
        expected_percussive_delta_db = energy_delta_db(
            percussive_before, percussive_after
        )
        expected_delta_db = expected_percussive_delta_db
        expected_distance = timbre_distance(mfcc_before, mfcc_after)
        assert result.energy_delta_db == pytest.approx(expected_delta_db)
        assert result.harmonic_delta_db == pytest.approx(expected_harmonic_delta_db)
        assert result.percussive_delta_db == pytest.approx(expected_percussive_delta_db)
        assert result.timbre_distance == pytest.approx(expected_distance)
        assert result.confidence == pytest.approx(
            confidence_score(expected_delta_db, expected_distance, config)
        )
        assert result.is_significant == is_significant_change(
            expected_delta_db, expected_distance, config
        )

    def test_quiet_unchanging_window_is_not_significant(self):
        config = AppConfig()
        mfcc = np.zeros(13)
        result = score_candidate(0.05, 0.05, 0.05, 0.05, mfcc, mfcc, config)
        assert result.is_significant is False

    def test_drop_requires_a_percussive_rise_not_a_matched_volume_rise(self):
        config = AppConfig(energy_change_threshold_db=4.0)
        mfcc = np.zeros(13)
        result = score_candidate(0.1, 0.1, 0.1, 0.3, mfcc, mfcc, config)

        assert result.percussive_delta_db > 4.0
        assert result.harmonic_delta_db == pytest.approx(0.0)
        assert result.is_significant is True

    def test_breakdown_requires_harmonic_energy_to_hold_or_rise(self):
        config = AppConfig(energy_change_threshold_db=4.0)
        mfcc = np.zeros(13)
        result = score_candidate(0.1, 0.2, 0.3, 0.1, mfcc, mfcc, config)

        assert result.percussive_delta_db < -4.0
        assert result.harmonic_delta_db > 0.0
        assert result.energy_delta_db < -4.0
        assert result.is_significant is True
