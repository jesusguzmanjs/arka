"""Pure HPSS-based structural scoring for Grid-Guided Phrase Analysis."""

from __future__ import annotations

import math
import numpy as np
from dataclasses import dataclass

from cuegrid.config import AppConfig

_EPS = 1e-10


@dataclass(frozen=True)
class ScoreResult:
    """HPSS and timbral evidence for one phrase-boundary candidate."""

    energy_delta_db: float
    harmonic_delta_db: float
    percussive_delta_db: float
    timbre_distance: float
    confidence: float
    is_significant: bool


def energy_delta_db(rms_before: float, rms_after: float) -> float:
    """Return a signed RMS ratio in dB, safely handling silence."""
    return 20.0 * math.log10(max(rms_after, _EPS) / max(rms_before, _EPS))


def timbre_distance(mfcc_before: np.ndarray, mfcc_after: np.ndarray) -> float:
    """Return Euclidean distance between two mean MFCC vectors."""
    return float(
        np.linalg.norm(
            np.asarray(mfcc_after, dtype=np.float64)
            - np.asarray(mfcc_before, dtype=np.float64)
        )
    )


def _structural_delta_db(harmonic_delta: float, percussive_delta: float) -> float:
    """Return the strongest signed HPSS structural clash.

    Positive values represent drop-like percussive arrivals. Negative values
    represent breakdown-like percussive removals while harmonic energy holds
    steady or rises. Harmonic movement in the opposite direction reduces a
    clash rather than turning ordinary master-volume changes into cues.
    """
    drop = max(0.0, percussive_delta - abs(harmonic_delta))
    breakdown = (
        max(0.0, -percussive_delta + harmonic_delta)
        if harmonic_delta >= 0.0
        else 0.0
    )
    return drop if drop >= breakdown else -breakdown


def confidence_score(
    structural_delta_db: float, timbre_distance_value: float, config: AppConfig
) -> float:
    """Combine HPSS structural contrast and timbral change for ranking."""
    return (
        abs(structural_delta_db) / config.energy_change_threshold_db
        + timbre_distance_value / config.timbre_change_distance_threshold
    )


def is_significant_change(
    structural_delta_db: float, timbre_distance_value: float, config: AppConfig
) -> bool:
    """Confirm a structural clash or an independently large timbral change."""
    return (
        abs(structural_delta_db) >= config.energy_change_threshold_db
        or timbre_distance_value >= config.timbre_change_distance_threshold
    )


def score_candidate(
    harmonic_before: float,
    harmonic_after: float,
    percussive_before: float,
    percussive_after: float,
    mfcc_before: np.ndarray,
    mfcc_after: np.ndarray,
    config: AppConfig,
) -> ScoreResult:
    """Score a candidate from precomputed harmonic/percussive HPSS features.

    A drop requires a percussive rise that is not merely mirrored by harmonic
    energy. A breakdown requires percussive loss while harmonic energy is
    steady or rising. The returned ``energy_delta_db`` is the signed
    structural contrast retained for legacy telemetry consumers.
    """
    harmonic_delta = energy_delta_db(harmonic_before, harmonic_after)
    percussive_delta = energy_delta_db(percussive_before, percussive_after)
    structural_delta = _structural_delta_db(harmonic_delta, percussive_delta)
    distance = timbre_distance(mfcc_before, mfcc_after)
    return ScoreResult(
        energy_delta_db=structural_delta,
        harmonic_delta_db=harmonic_delta,
        percussive_delta_db=percussive_delta,
        timbre_distance=distance,
        confidence=confidence_score(structural_delta, distance, config),
        is_significant=is_significant_change(structural_delta, distance, config),
    )
