"""Public audio-analysis interfaces used by CueGrid callers."""

from .beatgrid import PhraseCandidate, beat_length_ms, generate_phrase_candidates
from .features import (
    ScoreResult,
    confidence_score,
    energy_delta_db,
    is_significant_change,
    score_candidate,
    timbre_distance,
)
from .metadata import (
    MetadataWriteError,
    UnsupportedAudioFormatError,
    write_metadata_to_file,
)

__all__ = [
    "MetadataWriteError",
    "PhraseCandidate",
    "ScoreResult",
    "UnsupportedAudioFormatError",
    "beat_length_ms",
    "confidence_score",
    "energy_delta_db",
    "generate_phrase_candidates",
    "is_significant_change",
    "score_candidate",
    "timbre_distance",
    "write_metadata_to_file",
]
