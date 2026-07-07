"""Central application configuration.

Implements the ``AppConfig`` dataclass described in ``.openspec/2-spec.md``
section 2.2. All tunable thresholds used by ``audio.beatgrid``,
``audio.detector``, ``audio.features``, and ``core.pipeline`` live here so
they are never hard-coded inside logic modules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppConfig:
    # audio.beatgrid: phrase-candidate generation (spec section 4)
    phrase_beats: int = 16  # base phrase granularity, in beats (4-bar block)
    major_phrase_multiple: int = (
        2  # every Nth candidate is also an 8-bar (32-beat) "major" boundary
    )

    # audio.loader: decoding a small window around each candidate
    sample_rate: int | None = None  # None = keep native sample rate
    hop_length: int = 512  # frame hop used by librosa.feature.rms/mfcc within a window

    # audio.detector: sizing the before/after analysis window, scaled in beats (not seconds)
    # so it automatically adapts to the track's tempo.
    window_beats: float = 4.0  # 1 bar of context on each side of a candidate
    mfcc_count: int = 13

    # audio.features: significance thresholds for confirming a candidate as a real event
    energy_change_threshold_db: float = (
        3.0  # min |delta RMS| in dB to flag an energy change
    )
    timbre_change_distance_threshold: float = (
        12.0  # min Euclidean MFCC distance to flag a timbre change
    )

    # core.mapping: classification of confirmed candidates into labels
    intro_search_fraction: float = (
        0.25  # intro_end must fall within the first 25% of the track
    )
    outro_search_fraction: float = (
        0.20  # outro_start must fall within the last 20% of the track
    )
    max_drop_cues: int = 3  # cap on how many "drop" cues are written per track
