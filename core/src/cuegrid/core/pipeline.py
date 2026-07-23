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
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from cuegrid.audio.detector import DetectedEvent, detect_events
from cuegrid.audio.metadata import (
    MetadataWriteError,
    UnsupportedAudioFormatError,
    write_metadata_to_file,
)
from cuegrid.config import AppConfig
from cuegrid.core.mapping import map_events_to_cues
from cuegrid.nml.models import CuePoint, TrackEntry
from cuegrid.nml.parser import (
    AmbiguousTrackError,
    BatchTrackRef,
    NmlParser,
    TrackNotFoundError,
)
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


_METADATA_TEXT_FIELDS = {
    "title",
    "release",
    "artist",
    "remixer",
    "producer",
    "genre",
    "label",
    "comment",
    "comment2",
    "lyrics",
    "mix",
}
_METADATA_FIELDS = _METADATA_TEXT_FIELDS | {"rating"}


@dataclass
class MetadataTrackResult:
    """Outcome of one track in a standalone metadata batch."""

    path: str
    nml_updated: bool = False
    physical_file_updated: bool = False
    error: dict[str, str] | None = None


@dataclass
class MetadataBatchResult:
    """Ordered outcomes for one metadata batch request."""

    results: list[MetadataTrackResult]

    @property
    def nml_updated_count(self) -> int:
        return sum(result.nml_updated for result in self.results)

    @property
    def physical_file_updated_count(self) -> int:
        return sum(result.physical_file_updated for result in self.results)

    @property
    def error_count(self) -> int:
        return sum(result.error is not None for result in self.results)


@dataclass
class BatchSaveTrackResult:
    """Outcome for one committed track in a unified GUI save."""

    path: str
    nml_updated: bool = False
    physical_file_updated: bool = False
    error: dict[str, str] | None = None


@dataclass
class BatchSaveResult:
    results: list[BatchSaveTrackResult]

    @property
    def nml_updated_count(self) -> int:
        return sum(result.nml_updated for result in self.results)

    @property
    def physical_file_updated_count(self) -> int:
        return sum(result.physical_file_updated for result in self.results)

    @property
    def error_count(self) -> int:
        return sum(result.error is not None for result in self.results)


def validate_batch_save_payload(
    payload: object,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Validate the complete final-state track and playlist payload."""
    if not isinstance(payload, dict) or set(payload) - {"tracks", "playlists"}:
        raise ValueError("--batch-save must contain only tracks and/or playlists")
    tracks = payload.get("tracks", [])
    playlists = payload.get("playlists", [])
    if not isinstance(tracks, list) or not isinstance(playlists, list) or not (tracks or playlists):
        raise ValueError("tracks and playlists must be arrays with at least one mutation")

    normalized: list[dict[str, object]] = []
    paths: set[str] = set()
    allowed = {"path", "cues", "grid_anchor_ms", "bpm", "metadata"}
    for item in tracks:
        if not isinstance(item, dict) or set(item) - allowed or "path" not in item:
            raise ValueError("every batch track must contain path and only supported fields")
        path = item["path"]
        if not isinstance(path, str) or not path.strip() or path in paths:
            raise ValueError("track paths must be distinct non-empty strings")
        paths.add(path)
        if not any(key in item for key in allowed - {"path"}):
            raise ValueError("every batch track must update at least one field")

        output: dict[str, object] = {"path": path}
        if "cues" in item:
            output["cues"] = NmlWriter._validate_manual_cues(item["cues"])
        if "grid_anchor_ms" in item:
            grid = item["grid_anchor_ms"]
            if isinstance(grid, bool) or not isinstance(grid, (int, float)) or grid < 0:
                raise ValueError("grid_anchor_ms must be finite and non-negative")
            if not float("-inf") < float(grid) < float("inf"):
                raise ValueError("grid_anchor_ms must be finite and non-negative")
            output["grid_anchor_ms"] = float(grid)
        if "bpm" in item:
            bpm = item["bpm"]
            if isinstance(bpm, bool) or not isinstance(bpm, (int, float)):
                raise ValueError("bpm must be a finite number")
            output["bpm"] = NmlWriter._validate_bpm(float(bpm))
        if "metadata" in item:
            metadata = item["metadata"]
            if not isinstance(metadata, dict) or not metadata:
                raise ValueError("metadata must be a non-empty object")
            _, fields = validate_metadata_update_payload(
                {"track_paths": [path], "fields": metadata}
            )
            output["metadata"] = fields
        normalized.append(output)
    normalized_playlists: list[dict[str, object]] = []
    playlist_uuids: set[str] = set()
    for item in playlists:
        if not isinstance(item, dict) or "uuid" not in item or "action" not in item:
            raise ValueError("every playlist mutation must contain uuid and action")
        uuid = item["uuid"]
        action = item["action"]
        if not isinstance(uuid, str) or not uuid.strip() or uuid in playlist_uuids:
            raise ValueError("playlist UUIDs must be distinct non-empty strings")
        if action not in {"update", "delete"}:
            raise ValueError("playlist action must be update or delete")
        allowed = {"uuid", "action"} if action == "delete" else {"uuid", "action", "name", "entries"}
        if set(item) != allowed:
            raise ValueError(f"playlist {action} mutation has unsupported or missing fields")
        output: dict[str, object] = {"uuid": uuid, "action": action}
        if action == "update":
            name = item["name"]
            entries = item["entries"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("playlist name must be a non-empty string")
            if (not isinstance(entries, list) or any(not isinstance(path, str) or not path.strip() for path in entries)
                    or len(set(entries)) != len(entries)):
                raise ValueError("playlist entries must be distinct non-empty strings")
            output["name"] = name.strip()
            output["entries"] = entries
        playlist_uuids.add(uuid)
        normalized_playlists.append(output)
    return normalized, normalized_playlists


def run_batch_save_pipeline(
    nml_path: str | Path,
    payload: object,
    write_to_files: bool = False,
) -> BatchSaveResult:
    """Commit all final GUI track state in one NML transaction."""
    tracks, playlists = validate_batch_save_payload(payload)
    parser = NmlParser(nml_path)
    updates: list[tuple[ET.Element, list[dict] | None, float | None, float | None, dict[str, str | int | None] | None]] = []
    results: list[BatchSaveTrackResult] = []
    for track in tracks:
        path = cast(str, track["path"])
        element = parser.find_entry_element(path)
        cues = track.get("cues")
        updates.append((
            element,
            [{"hotcue": hotcue, "start_ms": start_ms} for hotcue, start_ms in cast(list[tuple[int, float]], cues)] if cues is not None else None,
            cast(float | None, track.get("grid_anchor_ms")),
            cast(float | None, track.get("bpm")),
            cast(dict[str, str | int | None] | None, track.get("metadata")),
        ))
        results.append(BatchSaveTrackResult(path=path))

    playlist_updates: list[tuple[ET.Element, str, str | None, list[str] | None]] = []
    playlist_nodes = {
        playlist_el.get("UUID"): node
        for node in parser.tree.getroot().iter("NODE")
        if node.get("TYPE") == "PLAYLIST"
        for playlist_el in [node.find("PLAYLIST")]
        if playlist_el is not None and playlist_el.get("UUID")
    }
    if len(playlist_nodes) != sum(
        1 for node in parser.tree.getroot().iter("NODE")
        if node.get("TYPE") == "PLAYLIST" and node.find("PLAYLIST") is not None and node.find("PLAYLIST").get("UUID")
    ):
        raise ValueError("playlist UUIDs must be unique in collection.nml")
    for playlist in playlists:
        uuid = cast(str, playlist["uuid"])
        node = playlist_nodes.get(uuid)
        if node is None:
            raise ValueError(f"playlist UUID not found: {uuid}")
        action = cast(str, playlist["action"])
        entry_keys: list[str] | None = None
        if action == "update":
            entry_keys = [NmlWriter._entry_to_primary_key(parser.find_entry_element(path)) for path in cast(list[str], playlist["entries"])]
        playlist_updates.append((node, action, cast(str | None, playlist.get("name")), entry_keys))

    NmlWriter(parser).write_batch_save(updates, playlist_updates)
    for result in results:
        result.nml_updated = True

    if write_to_files:
        for track, result in zip(tracks, results, strict=True):
            metadata = cast(dict[str, str | int | None] | None, track.get("metadata"))
            if metadata is None:
                continue
            try:
                write_metadata_to_file(result.path, metadata)
            except UnsupportedAudioFormatError as exc:
                result.error = {"code": "unsupported_audio_format", "message": str(exc)}
            except MetadataWriteError as exc:
                result.error = {"code": "physical_write_failed", "message": str(exc)}
            except Exception as exc:  # Mutagen and filesystem errors must not undo the committed NML.
                logger.exception("Unexpected physical metadata write failure for %r", result.path)
                result.error = {"code": "physical_write_failed", "message": str(exc)}
            else:
                result.physical_file_updated = True
    return BatchSaveResult(results=results)


def validate_metadata_update_payload(
    payload: object,
) -> tuple[list[str], dict[str, str | int | None]]:
    """Validate the complete JSON contract before any NML mutation."""
    if not isinstance(payload, dict):
        raise ValueError("--update-metadata must be a JSON object")
    if set(payload) != {"track_paths", "fields"}:
        raise ValueError("metadata payload must contain only track_paths and fields")

    track_paths = payload["track_paths"]
    if (
        not isinstance(track_paths, list)
        or not track_paths
        or any(not isinstance(path, str) or not path.strip() for path in track_paths)
    ):
        raise ValueError("track_paths must be a non-empty array of non-empty strings")
    if len(set(track_paths)) != len(track_paths):
        raise ValueError("track_paths must not contain duplicates")

    fields = payload["fields"]
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")
    unknown = set(fields) - _METADATA_FIELDS
    if unknown:
        raise ValueError(f"unsupported metadata field(s): {', '.join(sorted(unknown))}")

    normalized: dict[str, str | int | None] = {}
    for name, value in fields.items():
        if name == "rating":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5
            ):
                raise ValueError("rating must be null or an integer between 0 and 5")
        elif value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be a string or null")
        normalized[name] = value
    return track_paths, normalized


def run_metadata_update_pipeline(
    nml_path: str | Path,
    payload: object,
    write_to_files: bool = False,
    on_track_start: Callable[[MetadataTrackResult, int, int], None] | None = None,
    on_nml_status: Callable[[MetadataTrackResult], None] | None = None,
    on_mutagen_status: Callable[[MetadataTrackResult], None] | None = None,
) -> MetadataBatchResult:
    """Update NML metadata atomically, then optionally mirror tags to files.

    The NML operation is the source of truth and is committed once for all
    resolved tracks. Physical tag writes occur only after that commit and are
    deliberately isolated so an OS file lock cannot roll back or stop the
    batch.
    """
    track_paths, fields = validate_metadata_update_payload(payload)
    parser = NmlParser(nml_path)
    results = [MetadataTrackResult(path=path) for path in track_paths]
    updates: list[tuple[ET.Element, dict[str, str | int | None]]] = []

    for index, result in enumerate(results, start=1):
        if on_track_start is not None:
            on_track_start(result, index, len(results))
        try:
            element = parser.find_entry_element(result.path)
        except (TrackNotFoundError, AmbiguousTrackError) as exc:
            result.error = {"code": "nml_resolution_failed", "message": str(exc)}
            continue
        updates.append((element, fields))

    if updates:
        writer = NmlWriter(parser)
        writer.write_metadata_batch(updates)
        for result in results:
            if result.error is None:
                result.nml_updated = True
                if on_nml_status is not None:
                    on_nml_status(result)

    if not write_to_files:
        return MetadataBatchResult(results=results)

    for result in results:
        if not result.nml_updated:
            continue
        try:
            write_metadata_to_file(result.path, fields)
        except UnsupportedAudioFormatError as exc:
            result.error = {"code": "unsupported_audio_format", "message": str(exc)}
            if on_mutagen_status is not None:
                on_mutagen_status(result)
        except MetadataWriteError as exc:
            logger.warning("Physical metadata write failed for %r: %s", result.path, exc)
            result.error = {"code": "physical_write_failed", "message": str(exc)}
            if on_mutagen_status is not None:
                on_mutagen_status(result)
        else:
            result.physical_file_updated = True
            if on_mutagen_status is not None:
                on_mutagen_status(result)

    return MetadataBatchResult(results=results)


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
    track_paths: list[str] | None = None,
    artist: str | None = None,
    clear_existing: bool = False,
    on_track_complete: Callable[[BatchTrackResult], None] | None = None,
) -> BatchResult:
    """Run the Grid-Guided Phrase Analysis pipeline for multiple tracks.

    Implements ``.openspec/2-spec.md`` section 8.3: batch processing with
    error isolation. Exactly one of `playlist`, `track_title`, or
    `track_paths` must be given. Processes each resolved track sequentially,
    mutating the retained NML tree in memory, then commits all detected cues
    with one backup and atomic write after the batch completes.

    Args:
        nml_path: Path to the Traktor ``collection.nml`` to read from and
            write back to.
        config: Tunable thresholds; defaults to ``AppConfig()``.
        playlist: Batch select by Traktor playlist name (spec section 8.1).
        track_title: Batch select by track TITLE (spec section 8.2),
            optionally narrowed by `artist`.
        track_paths: Batch select by explicit audio paths. Each path is
            independently resolved, so an unresolved path is logged and does
            not stop the remaining batch.
        artist: Optional artist filter to narrow `track_title` search
            (spec section 8.2); not allowed together with `playlist`.
        clear_existing: If ``True``, clear existing standard HotCues from
            each entry before writing new ones.

    Returns:
        A ``BatchResult`` summarizing all processed and skipped tracks.
        No exception from any single track's processing propagates out.

    Raises:
        ValueError: if exactly one selection mode is not given, or if `artist`
            is given together with `playlist`.
        PlaylistNotFoundError: if the playlist name does not exist.
        AmbiguousPlaylistError: if the playlist name matches multiple playlists.
        TrackNotFoundError: if `track_title` matches no entries.
    """
    config = config or AppConfig()
    reset_telemetry_cache()

    # Validation: exactly one selection mode.
    selection_count = sum(
        bool(value) if value is track_paths else value is not None
        for value in (playlist, track_title, track_paths)
    )
    if selection_count != 1:
        raise ValueError(
            "Exactly one of 'playlist', 'track_title', or 'track_paths' must be given"
        )

    if playlist is not None and artist is not None:
        raise ValueError("'artist' is not allowed together with 'playlist'")

    # Resolve batch entries
    parser = NmlParser(nml_path)

    batch_refs: list[BatchTrackRef]
    if playlist is not None:
        batch_refs = parser.find_entries_by_playlist(playlist)
    elif track_title is not None:
        batch_refs = parser.find_entries_by_title(track_title, artist=artist)
    else:
        assert track_paths is not None
        batch_refs = []
        for track_path in track_paths:
            try:
                entry = parser.find_entry(track_path, artist=artist)
                element = parser.find_entry_element(track_path, artist=artist)
            except (TrackNotFoundError, AmbiguousTrackError) as exc:
                logger.error("Skipping unresolved track path %r: %s", track_path, exc)
                continue
            batch_refs.append(BatchTrackRef(entry=entry, element=element))

    logger.info("Resolved %d track(s) for batch processing", len(batch_refs))

    total_tracks = len(batch_refs)

    # The backup captures the unmodified collection once for the complete
    # batch.  Individual track processing only mutates the retained tree;
    # writing inside the loop would rewrite the complete NML per track.
    writer = NmlWriter(parser)
    if batch_refs:
        writer._backup_if_needed()

    # Process each track in memory.
    results: list[BatchTrackResult] = []
    has_cue_mutations = False

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
            has_cue_mutations = True

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

    if has_cue_mutations:
        writer._write_atomic()
        logger.info("Wrote batch AutoCue updates to %s", nml_path)

    return BatchResult(results=results)
