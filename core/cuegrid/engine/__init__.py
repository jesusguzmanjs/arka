"""Public orchestration and Smart Playlist interfaces used by CueGrid callers."""

from .mapping import map_event_to_cue, map_events_to_cues
from .pipeline import (
    BatchResult,
    BatchSaveResult,
    BatchSaveTrackResult,
    BatchTrackResult,
    MetadataBatchResult,
    MetadataTrackResult,
    PipelineResult,
    run_batch_pipeline,
    run_batch_save_pipeline,
    run_metadata_update_pipeline,
    run_pipeline,
    serialize_gui_payload,
    validate_batch_save_payload,
    validate_metadata_update_payload,
)
from .smart_playlist import matches_rule, matches_rules

__all__ = [
    "BatchResult",
    "BatchSaveResult",
    "BatchSaveTrackResult",
    "BatchTrackResult",
    "MetadataBatchResult",
    "MetadataTrackResult",
    "PipelineResult",
    "map_event_to_cue",
    "map_events_to_cues",
    "matches_rule",
    "matches_rules",
    "run_batch_pipeline",
    "run_batch_save_pipeline",
    "run_metadata_update_pipeline",
    "run_pipeline",
    "serialize_gui_payload",
    "validate_batch_save_payload",
    "validate_metadata_update_payload",
]
