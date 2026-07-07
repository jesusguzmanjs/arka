"""Pure math scoring for Grid-Guided Phrase Analysis.

Implements the scoring formulas from ``.openspec/2-spec.md`` section 6.1,
step 5: given precomputed RMS energy and MFCC timbre vectors for the
"before" and "after" windows around a phrase candidate, decide whether the
change between them is significant enough to confirm a structural event.

Per the module boundaries in section 2.1, this module never calls
``librosa`` and never touches files -- it is a pure function of
scalar/vector inputs, so it can be tested and reasoned about in isolation
from anything audio-decoding-related.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from traktorco.config import AppConfig

# Floor to avoid log10(0) / division-by-zero on silent windows.
_EPS = 1e-10


@dataclass
class ScoreResult:
    """The combined score for a single phrase candidate (spec section 6.1)."""

    energy_delta_db: float
    timbre_distance: float
    confidence: float
    is_significant: bool


def energy_delta_db(rms_before: float, rms_after: float) -> float:
    """Return the change in RMS energy across a candidate, in decibels.

    Implements ``.openspec/2-spec.md`` section 6.1, step 5::

        energy_delta_db = 20 * log10(max(rms_after, eps) / max(rms_before, eps))

    Positive values mean energy is rising at the candidate (a `drop`
    signature); negative values mean energy is falling.
    """
    return 20.0 * math.log10(max(rms_after, _EPS) / max(rms_before, _EPS))


def timbre_distance(mfcc_before: np.ndarray, mfcc_after: np.ndarray) -> float:
    """Return the Euclidean distance between two mean MFCC vectors.

    Implements ``.openspec/2-spec.md`` section 6.1, step 5::

        timbre_distance = euclidean(mfcc_after, mfcc_before)
    """
    before = np.asarray(mfcc_before, dtype=float)
    after = np.asarray(mfcc_after, dtype=float)
    return float(np.linalg.norm(after - before))


def confidence_score(
    energy_delta_db_value: float, timbre_distance_value: float, config: AppConfig
) -> float:
    """Combine energy and timbre change into a single confidence score.

    Implements ``.openspec/2-spec.md`` section 6.1, step 5::

        confidence = |energy_delta_db| / energy_change_threshold_db
                     + timbre_distance / timbre_change_distance_threshold

    An arbitrary positive scale (not a probability) used only to rank
    candidates against each other, e.g. when picking the top `drop`
    candidates (spec section 6.1, step 6).
    """
    return (
        abs(energy_delta_db_value) / config.energy_change_threshold_db
        + timbre_distance_value / config.timbre_change_distance_threshold
    )


def is_significant_change(
    energy_delta_db_value: float, timbre_distance_value: float, config: AppConfig
) -> bool:
    """Decide whether a candidate's before/after change is significant.

    Implements ``.openspec/2-spec.md`` section 6.1, step 5::

        is_significant = |energy_delta_db| >= energy_change_threshold_db
                          or timbre_distance >= timbre_change_distance_threshold
    """
    return (
        abs(energy_delta_db_value) >= config.energy_change_threshold_db
        or timbre_distance_value >= config.timbre_change_distance_threshold
    )


def score_candidate(
    rms_before: float,
    rms_after: float,
    mfcc_before: np.ndarray,
    mfcc_after: np.ndarray,
    config: AppConfig,
) -> ScoreResult:
    """Score a single phrase candidate's before/after windows.

    Implements the full scoring pipeline from ``.openspec/2-spec.md``
    section 6.1, step 5, as a single convenience entry point:
    ``(rms_before, rms_after, mfcc_before, mfcc_after, config) ->
    ScoreResult(energy_delta_db, timbre_distance, confidence,
    is_significant)``.
    """
    delta_db = energy_delta_db(rms_before, rms_after)
    distance = timbre_distance(mfcc_before, mfcc_after)
    return ScoreResult(
        energy_delta_db=delta_db,
        timbre_distance=distance,
        confidence=confidence_score(delta_db, distance, config),
        is_significant=is_significant_change(delta_db, distance, config),
    )
