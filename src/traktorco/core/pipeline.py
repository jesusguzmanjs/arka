"""Single-track orchestration pipeline.

Implements the ``core.pipeline`` responsibility from
``.openspec/2-spec.md`` section 2.1, and the end-to-end flow in section 5:
parse NML -> generate phrase candidates (inside ``audio.detector``) ->
targeted detection -> map -> write, for exactly one track.

This module contains no XML- or DSP-specific logic itself (spec section
2.1) -- it only wires together already-implemented, independently
testable modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from traktorco.audio.detector import DetectedEvent, detect_events
from traktorco.config import AppConfig
from traktorco.core.mapping import map_events_to_cues
from traktorco.nml.models import CuePoint, TrackEntry
from traktorco.nml.parser import NmlParser
from traktorco.nml.writer import NmlWriter

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Summary of a single track run, for CLI/logging consumption."""

    entry: TrackEntry
    detected_events: list[DetectedEvent]
    written_cues: list[CuePoint]


def run_pipeline(
    nml_path: str | Path,
    track_path: str | Path,
    config: AppConfig | None = None,
    title: str | None = None,
    artist: str | None = None,
) -> PipelineResult:
    """Run the full Grid-Guided Phrase Analysis pipeline for one track.

    Implements ``.openspec/2-spec.md`` section 5:

        parse NML -> extract BPM/grid anchor/duration -> generate phrase
        candidates (inside audio.detector, section 4) -> targeted
        detection (section 6) -> map to CuePoints (section 3.4) -> write
        back to the NML (section 3.4).

    Args:
        nml_path: Path to the Traktor ``collection.nml`` to read from and
            write back to.
        track_path: Path to the audio file to analyze; also used to
            locate the matching ``<ENTRY>`` (spec section 7).
        config: Tunable thresholds; defaults to ``AppConfig()``.
        title: Optional disambiguation filter, used if ``track_path``
            alone matches more than one ``<ENTRY>`` (spec section 7.3,
            step 6).
        artist: Optional disambiguation filter, same purpose.

    Returns:
        A ``PipelineResult`` summarizing what was found and written.

    Raises:
        TrackNotFoundError: if no ``<ENTRY>`` matches ``track_path``.
        AmbiguousTrackError: if more than one ``<ENTRY>`` matches, even
            after applying any ``title``/``artist`` filters given.
        HotcueSlotConflictError: if ``core.mapping`` somehow produced a
            ``CuePoint`` whose slot collides with an existing one (should
            not happen in practice -- mapping only assigns free slots).
    """
    config = config or AppConfig()

    parser = NmlParser(nml_path)
    entry = parser.find_entry(track_path, title=title, artist=artist)

    logger.info(
        "Matched ENTRY %r - %r (BPM=%.3f, grid_anchor_ms=%.3f, duration_ms=%.3f)",
        entry.artist,
        entry.title,
        entry.tempo.bpm,
        entry.grid_anchor_ms,
        entry.duration_ms,
    )

    events = detect_events(
        audio_path=track_path,
        bpm=entry.tempo.bpm,
        grid_anchor_ms=entry.grid_anchor_ms,
        duration_ms=entry.duration_ms,
        config=config,
    )
    logger.info("Detected %d event(s)", len(events))

    new_cues = map_events_to_cues(events, entry.cues)
    logger.info("Mapped %d event(s) to free HOTCUE slots", len(new_cues))

    if new_cues:
        writer = NmlWriter(parser)
        writer.write_cues(track_path, new_cues, title=title, artist=artist)
        logger.info("Wrote %d new CUE_V2 element(s) to %s", len(new_cues), nml_path)
    else:
        logger.info("No cues to write; %s left untouched", nml_path)

    return PipelineResult(entry=entry, detected_events=events, written_cues=new_cues)
