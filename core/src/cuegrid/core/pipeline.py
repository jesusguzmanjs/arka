"""Single-track and batch orchestration pipeline.

Implements the ``core.pipeline`` responsibility from
``.openspec/2-spec.md`` section 2.1, and the end-to-end flow in section 5:
parse NML -> generate phrase candidates (inside ``audio.detector``) ->
targeted detection -> map -> write, for exactly one track.

Also implements section 8 (batch processing), which extends the pipeline
to handle multiple tracks selected by playlist or title.

This module contains no XML- or DSP-specific logic itself (spec section
2.1) -- it only wires together already-implemented, independently
testable modules.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cuegrid.audio.detector import DetectedEvent, detect_events
from cuegrid.config import AppConfig
from cuegrid.core.mapping import map_events_to_cues
from cuegrid.nml.models import CuePoint, TrackEntry
from cuegrid.nml.parser import BatchTrackRef, NmlParser
from cuegrid.nml.writer import NmlWriter
from cuegrid.telemetry import reset_telemetry_cache

logger = logging.getLogger(__name__)



@dataclass
class PipelineResult:
    """Summary of a single track run, for CLI/logging consumption."""

    entry: TrackEntry
    detected_events: list[DetectedEvent]
    written_cues: list[CuePoint]
    skipped_reason: str | None = None


# GUI export uses a small tolerance only for floating-point representation;
# cue validity remains determined by the mathematical BPM/grid-anchor position.
_GUI_GRID_TOLERANCE_MS = 1e-3


def serialize_gui_payload(result: PipelineResult, track_path: str | Path) -> str:
    """Serialize one completed analysis as the GUI's single JSON document.

    This function deliberately returns a string rather than printing it. The
    CLI owns stdout framing so ``--export-gui`` can guarantee that no other
    output is written to the stream. Every numeric conversion happens before
    ``json.dumps`` because analysis values may originate from NumPy scalars.
    """
    bpm = float(result.entry.tempo.bpm)
    grid_anchor_ms = float(result.entry.grid_anchor_ms)
    duration_ms = float(result.entry.duration_ms)
    beat_ms = 60_000.0 / bpm if bpm > 0.0 else 0.0

    cues: list[dict[str, object]] = []
    for cue in result.written_cues:
        position_ms = float(cue.start_ms)
        if beat_ms > 0.0:
            beat_number = round((position_ms - grid_anchor_ms) / beat_ms)
            nearest_grid_ms = grid_anchor_ms + float(beat_number) * beat_ms
            is_valid = (
                abs(position_ms - nearest_grid_ms) <= _GUI_GRID_TOLERANCE_MS
            )
        else:
            is_valid = False

        cues.append(
            {
                "id": int(cue.hotcue),
                "position_ms": position_ms,
                "is_valid": bool(is_valid),
            }
        )

    payload = {
        "track_path": str(Path(track_path).expanduser().resolve()),
        "bpm": bpm,
        "grid_anchor_ms": grid_anchor_ms,
        "is_flex_grid": bool(result.entry.is_flex_grid),
        "duration_ms": duration_ms,
        "cues": cues,
    }
    return json.dumps(payload, separators=(",", ":"), allow_nan=False)


@dataclass
class BatchTrackResult:
    """Result for one track processed in a batch."""

    entry: TrackEntry
    detected_events: list[DetectedEvent] | None  # None if track was skipped
    written_cues: list[CuePoint] = field(default_factory=list)
    error: str | None = None  # None on success, else a human-readable reason

    # v2.3 JSON streaming: populated by run_batch_pipeline for consumers that
    # want per-track progress (e.g. a GUI sidecar consuming NDJSON via the
    # on_track_complete callback). 0 = not yet set / single-track mode.
    index: int = 0
    total: int = 0


@dataclass
class BatchResult:
    """Summary of a batch processing run."""

    results: list[BatchTrackResult]

    @property
    def succeeded_count(self) -> int:
        """Count of tracks successfully processed (detected_events is not None)."""
        return sum(1 for r in self.results if r.detected_events is not None)

    @property
    def skipped_count(self) -> int:
        """Count of tracks skipped (detected_events is None)."""
        return sum(1 for r in self.results if r.detected_events is None)


def run_pipeline(
    nml_path: str | Path,
    track_path: str | Path,
    config: AppConfig | None = None,
    title: str | None = None,
    artist: str | None = None,
    clear_existing: bool = False,
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
        clear_existing: If ``True``, clear existing standard HotCues from
            the entry before writing new ones, so all slots are free to
            reuse. Grid/Beatport markers are never removed.

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
    reset_telemetry_cache()

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

    if entry.is_flex_grid:
        logger.warning(
            "Skipping %r - %r: Flex Grid / variable BPM unsupported",
            entry.artist,
            entry.title,
        )
        return PipelineResult(
            entry=entry,
            detected_events=[],
            written_cues=[],
            skipped_reason="flex_grid",
        )

    events = detect_events(
        audio_path=track_path,
        bpm=entry.tempo.bpm,
        grid_anchor_ms=entry.grid_anchor_ms,
        duration_ms=entry.duration_ms,
        config=config,
        track_title=f"{entry.artist} - {entry.title}",
        peak_db=entry.peak_db,
        perceived_db=entry.perceived_db,
    )

    logger.info("Detected %d event(s)", len(events))
    new_cues = map_events_to_cues(
        events,
        entry.cues,
        clear_existing=clear_existing,
        bpm=entry.tempo.bpm,
        grid_anchor_ms=entry.grid_anchor_ms,
    )
    logger.info("Mapped %d event(s) to free HOTCUE slots", len(new_cues))

    if new_cues:
        writer = NmlWriter(parser)
        writer.write_cues(
            track_path,
            new_cues,
            title=title,
            artist=artist,
            clear_existing=clear_existing,
        )
        logger.info("Wrote %d new CUE_V2 element(s) to %s", len(new_cues), nml_path)
    else:
        logger.info("No cues to write; %s left untouched", nml_path)

    return PipelineResult(entry=entry, detected_events=events, written_cues=new_cues)


def run_batch_pipeline(
    nml_path: str | Path,
    config: AppConfig | None = None,
    playlist: str | None = None,
    track_title: str | None = None,
    artist: str | None = None,
    clear_existing: bool = False,
    on_track_complete: Callable[[BatchTrackResult], None] | None = None,
) -> BatchResult:
    """Run the Grid-Guided Phrase Analysis pipeline for multiple tracks.

    Implements ``.openspec/2-spec.md`` section 8.3: batch processing with
    error isolation. Exactly one of `playlist` or `track_title` must be
    given. Processes each resolved track sequentially, writing cues
    immediately after each track succeeds.

    Args:
        nml_path: Path to the Traktor ``collection.nml`` to read from and
            write back to.
        config: Tunable thresholds; defaults to ``AppConfig()``.
        playlist: Batch select by Traktor playlist name (spec section 8.1).
        track_title: Batch select by track TITLE (spec section 8.2),
            optionally narrowed by `artist`.
        artist: Optional artist filter to narrow `track_title` search
            (spec section 8.2); not allowed together with `playlist`.
        clear_existing: If ``True``, clear existing standard HotCues from
            each entry before writing new ones.

    Returns:
        A ``BatchResult`` summarizing all processed and skipped tracks.
        No exception from any single track's processing propagates out.

    Raises:
        ValueError: if neither or both of `playlist`/`track_title` are given,
            or if `artist` is given together with `playlist`.
        PlaylistNotFoundError: if the playlist name does not exist.
        AmbiguousPlaylistError: if the playlist name matches multiple playlists.
        TrackNotFoundError: if `track_title` matches no entries.
    """
    config = config or AppConfig()
    reset_telemetry_cache()

    # Validation: exactly one selection mode
    if (playlist is None and track_title is None) or (
        playlist is not None and track_title is not None
    ):
        raise ValueError("Exactly one of 'playlist' or 'track_title' must be given")

    if playlist is not None and artist is not None:
        raise ValueError("'artist' is not allowed together with 'playlist'")

    # Resolve batch entries
    parser = NmlParser(nml_path)

    batch_refs: list[BatchTrackRef]
    if playlist is not None:
        batch_refs = parser.find_entries_by_playlist(playlist)
    else:
        assert track_title is not None
        batch_refs = parser.find_entries_by_title(track_title, artist=artist)

    logger.info("Resolved %d track(s) for batch processing", len(batch_refs))

    total_tracks = len(batch_refs)

    # Process each track
    writer = NmlWriter(parser)
    results: list[BatchTrackResult] = []

    for i, batch_ref in enumerate(batch_refs, 1):
        entry = batch_ref.entry
        element = batch_ref.element

        # Flex Grid guard: multiple grid anchors cannot safely support the
        # fixed-BPM phrase mathematics, so never decode or mutate the entry.
        if entry.is_flex_grid:
            logger.warning(
                "Skipping %r - %r: Flex Grid / variable BPM unsupported",
                entry.artist,
                entry.title,
            )
            track_result = BatchTrackResult(
                entry=entry,
                detected_events=None,
                error="flex_grid",
                index=i,
                total=total_tracks,
            )
            results.append(track_result)
            if on_track_complete is not None:
                on_track_complete(track_result)
            continue

        # BPM guard (spec section 8.3, step 1)
        if entry.tempo.bpm <= 0:
            logger.warning(
                "Skipping %r - %r: missing or invalid BPM (%.3f)",
                entry.artist,
                entry.title,
                entry.tempo.bpm,
            )
            track_result = BatchTrackResult(
                entry=entry,
                detected_events=None,
                error="missing or invalid BPM",
                index=i,
                total=total_tracks,
            )
            results.append(track_result)
            if on_track_complete is not None:
                on_track_complete(track_result)
            continue

        # Detection with broad error handling (spec section 8.3, step 2)
        events: list[DetectedEvent] | None = None
        new_cues: list[CuePoint] = []
        try:
            events = detect_events(
                audio_path=entry.location_path,
                bpm=entry.tempo.bpm,
                grid_anchor_ms=entry.grid_anchor_ms,
                duration_ms=entry.duration_ms,
                config=config,
                track_title=f"{entry.artist} - {entry.title}",
                peak_db=entry.peak_db,
                perceived_db=entry.perceived_db,
            )
        except Exception as exc:
            logger.warning(
                "Skipping %r - %r: audio analysis failed: %s",
                entry.artist,
                entry.title,
                str(exc),
            )
            track_result = BatchTrackResult(
                entry=entry,
                detected_events=None,
                error=str(exc),
                index=i,
                total=total_tracks,
            )
            results.append(track_result)
            if on_track_complete is not None:
                on_track_complete(track_result)
            continue

        logger.info("Detected %d event(s) in %r - %r", len(events), entry.artist, entry.title)
        new_cues = map_events_to_cues(
            events,
            entry.cues,
            clear_existing=clear_existing,
            bpm=entry.tempo.bpm,
            grid_anchor_ms=entry.grid_anchor_ms,
        )
        logger.info("Mapped %d event(s) to free HOTCUE slots", len(new_cues))

        if new_cues:
            writer.write_cues_to_element(
                element, new_cues, clear_existing=clear_existing
            )
            writer._backup_if_needed()
            writer._write_atomic()
            logger.info("Wrote %d new CUE_V2 element(s) to %s", len(new_cues), nml_path)

        # Record success (spec section 8.3, step 5)
        track_result = BatchTrackResult(
            entry=entry,
            detected_events=events,
            written_cues=new_cues,
            error=None,
            index=i,
            total=total_tracks,
        )
        results.append(track_result)

        if on_track_complete is not None:
            on_track_complete(track_result)

    return BatchResult(results=results)
