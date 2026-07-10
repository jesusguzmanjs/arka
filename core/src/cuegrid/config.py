"""Central application configuration.

Implements the ``AppConfig`` dataclass described in ``.openspec/2-spec.md``
section 2.2. All tunable thresholds used by ``audio.beatgrid``,
``audio.detector``, ``audio.features``, and ``core.pipeline`` live here so
they are never hard-coded inside logic modules.
"""

from __future__ import annotations

from dataclasses import dataclass

# v1.4: Dynamic sensitivity presets. Each mode bundles the
# (energy_threshold_db, timbre_threshold, relative_confidence_threshold)
# triple. When ``--mode`` is supplied on the CLI, these values override any
# individual threshold flags.
DETECTION_MODES: dict[str, tuple[float, float, float]] = {
    "soft": (2.0, 8.0, 0.15),
    "medium": (4.0, 18.0, 0.30),
    "hard": (7.0, 30.0, 0.50),
}


@dataclass
class AppConfig:
    # audio.beatgrid: phrase-candidate generation (spec section 4)
    phrase_beats: int = 8  # base phrase granularity, in beats (2-bar block)
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
    # Medium sensitivity is the default preset; --mode binds all three values together.
    energy_change_threshold_db: float = (
        4.0  # min |delta RMS| in dB to flag an energy change
    )
    timbre_change_distance_threshold: float = (
        18.0  # min Euclidean MFCC distance to flag a timbre change
    )

    # audio.detector: unified structural-cue selection (spec section 6)
    max_cues: int = 8  # cap on how many cues are written per track
    relative_confidence_threshold: float = (
        0.30  # keep only candidates >= this fraction of the track's max confidence
    )

    # v1.8 data export: write per-candidate telemetry to a CSV for offline tuning
    export_csv_path: str | None = None

    # v1.10: dynamic sensitivity mode (None = use individual thresholds)
    detection_mode: str | None = None

    # v2.1: explicit override for Traktor's native Stems/ root directory.
    # None = auto-discover (nml.stems.resolve_stem_path's Music-folder-first,
    # NML-sibling-fallback order; spec section 9.6).
    stems_dir: str | None = None

    # v2.2/v1.5: Stems-active Multi-Source Validation defaults to "smart".
    # --no-stems forces analysis of the original Master file and bypasses
    # native Stem lookup entirely; Option C still protects Stems-active runs
    # from empty/ambient drum stems (section 10).
    verify: str = "smart"
    no_stems: bool = False
