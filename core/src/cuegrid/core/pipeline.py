"""Single-track and batch orchestration pipeline.

Implements the ``core.pipeline`` responsibility from
``.openspec/2-spec.md`` section 2.1, and the end-to-end flow in section 5:
parse NML -> generate phrase candidates (inside ``audio.detector``) ->
targeted detection -> map -> write, for exactly one track.

Also implements section 8 (batch processing), which extends the pipeline
to handle multiple tracks selected by playlist or title.

v2.0 Stems Integration (spec section 9) additionally intercepts the audio
source fed to ``audio.detector`` for tracks that have a native Traktor
stem sidecar: if ``entry.flags`` indicates a stem exists (spec section
9.1) and the predicted sidecar (``nml.stems.resolve_stem_path``) exists
on disk, its isolated Drums/Rhythm stream is extracted
(``audio.loader.extract_drum_stem``) and analyzed instead of the original
mixed track. This never changes detection math or NML writing -- it only
swaps which file ``detect_events`` reads samples from.

v2.2 Multi-Source Validation (spec section 10) adds two further,
independent refinements on top of that stem-swap:

- **Empty Stem Detection (section 10.1):** immediately after extracting
  a drum stem, ``_resolve_analysis_source`` runs a lightning-fast energy
  probe (``audio.loader.is_drum_stem_empty``) and falls back to the
  original Master audio if the stem is practically silent/ambient (e.g.
  Ambient or IDM tracks with no real drum content). This runs
  unconditionally -- in both ``--verify fast`` and ``--verify smart``
  (the default) -- since it protects *any* mode from analyzing silence.
- **Smart Validation Gating (section 10.2):** when ``--verify smart`` is
  given and a valid (non-empty) drum stem was actually used, each
  confirmed ``DetectedEvent``'s timestamp is cross-checked against a
  small, targeted window of the original Master audio to classify it as
  a rhythm-driven "Drop (Rhythm)" or a melodic "Breakdown (Melodic)",
  relabeling the resulting ``CuePoint``. This only ever does targeted
  micro-reads of the Master file (never a full decode), keeping smart
  mode's overhead tight.

This module contains no XML- or DSP-specific logic itself (spec section
2.1) -- it only wires together already-implemented, independently
testable modules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cuegrid.audio.detector import DetectedEvent, detect_events
from cuegrid.audio.loader import extract_drum_stem, is_drum_stem_empty, load_window
from cuegrid.config import AppConfig
from cuegrid.core.mapping import map_events_to_cues
from cuegrid.nml.models import CuePoint, TrackEntry
from cuegrid.nml.parser import BatchTrackRef, NmlParser
from cuegrid.nml.stems import has_stem_flag, resolve_stem_path
from cuegrid.nml.writer import NmlWriter
from cuegrid.telemetry import reset_telemetry_cache

logger = logging.getLogger(__name__)

# v2.2 Smart Validation Gating (spec section 10.2): a 2-second window,
# centered on each candidate's timestamp, is enough to distinguish a
# rhythm-driven hit from a melodic passage without ever decoding more than
# a couple of seconds of the Master file per candidate.
_SMART_VALIDATION_WINDOW_SEC = 2.0

# Below this mean RMS, a window is considered "low" energy for smart
# classification purposes (spec section 10.2). Reuses the same order of
# magnitude as audio.loader.DRUM_STEM_SILENCE_RMS_THRESHOLD, since both are
# judging "is there meaningful signal here at all".
_SMART_HIGH_ENERGY_RMS_THRESHOLD = 0.02

_LABEL_DROP_RHYTHM = "Drop (Rhythm)"
_LABEL_BREAKDOWN_MELODIC = "Breakdown (Melodic)"


@dataclass
class PipelineResult:
    """Summary of a single track run, for CLI/logging consumption."""

    entry: TrackEntry
    detected_events: list[DetectedEvent]
    written_cues: list[CuePoint]


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


def _resolve_analysis_source(
    entry: TrackEntry,
    fallback_path: str | Path,
    nml_path: str | Path,
    stems_dir: str | Path | None = None,
    no_stems: bool = False,
) -> tuple[str | Path, Path | None]:
    """Pick which audio file to feed the detector for one track.

    Implements the "Pipeline Interception" step of the v2.0 Stems
    Integration architecture (spec section 9.3), and the v2.1 "Smart
    Stems Path" fix (spec section 9.6):

    1. If ``no_stems`` is true, bypass the ``entry.flags`` bitmask check,
       stem path prediction, and extraction entirely; return the original
       ``fallback_path`` unchanged.
    2. If ``entry.flags`` does not carry the native-stem bit (spec
       section 9.1), fall back to ``fallback_path`` unchanged.
    3. Otherwise, predict the sidecar path (``nml.stems.resolve_stem_path``),
       forwarding ``stems_dir`` unchanged so an explicit CLI/config
       override -- or the module's own Music-folder-first auto-discovery
       when it is ``None`` -- is respected. If prediction fails (e.g. no
       ``AUDIO_ID``) or the predicted file does not exist on disk, fall
       back to ``fallback_path`` unchanged -- this is a graceful
       degradation, never an error.
    4. Otherwise, extract the isolated Drums/Rhythm stream from the
       sidecar to a temporary WAV file (``audio.loader.extract_drum_stem``)
       and use that as the analysis source. If extraction itself fails
       (e.g. ffmpeg error, corrupt sidecar), log a warning and fall back
       to ``fallback_path`` rather than aborting the track.
    5. v2.2 Empty Stem Detection (spec section 10.1): run a lightning-fast
       energy probe (``audio.loader.is_drum_stem_empty``) on the freshly
       extracted stem. If it is practically silent/ambient (e.g. an
       Ambient or IDM track with no real drum content), delete the temp
       WAV and fall back to ``fallback_path`` -- this protects fast mode
       from analyzing silence, and runs unconditionally regardless of
       ``--verify``.

    Args:
        entry: The parsed ``TrackEntry``, used for its ``flags`` and
            ``audio_id``.
        fallback_path: The original audio file path to use when no stem
            is available or usable.
        nml_path: Path to the ``collection.nml`` this entry came from, so
            the NML-sibling ``Stems/`` fallback root can be located if
            needed.
        stems_dir: Optional explicit override for the ``Stems/`` root
            directory (``AppConfig.stems_dir``/``--stems-dir``). ``None``
            defers to ``resolve_stem_path``'s own auto-discovery.
        no_stems: When true, force the original Master audio and bypass all
            native Stem availability and lookup logic.

    Returns:
        A ``(analysis_path, temp_stem_wav)`` tuple. ``analysis_path`` is
        what callers should pass to ``detect_events``. ``temp_stem_wav``
        is ``None`` unless a temporary drum-stem WAV was created, in
        which case the caller is responsible for deleting it once
        analysis is complete.
    """
    if no_stems:
        logger.info(
            "Native Stems disabled for %r - %r; using original Master audio",
            entry.artist,
            entry.title,
        )
        return fallback_path, None

    if not has_stem_flag(entry.flags):
        return fallback_path, None

    try:
        stem_path = resolve_stem_path(entry, nml_path, stems_dir=stems_dir)
    except Exception as exc:
        logger.warning(
            "Stem path prediction failed for %r - %r: %s; falling back to "
            "original audio",
            entry.artist,
            entry.title,
            exc,
        )
        return fallback_path, None

    if stem_path is None or not stem_path.is_file():
        logger.info(
            "No native stem sidecar found on disk for %r - %r "
            "(predicted: %s); falling back to original audio",
            entry.artist,
            entry.title,
            stem_path,
        )
        return fallback_path, None

    try:
        drum_wav = extract_drum_stem(stem_path)
    except Exception as exc:
        logger.warning(
            "Drum stem extraction failed for %r - %r (%s): %s; falling "
            "back to original audio",
            entry.artist,
            entry.title,
            stem_path,
            exc,
        )
        return fallback_path, None

    try:
        stem_is_empty = is_drum_stem_empty(drum_wav)
    except Exception as exc:
        logger.warning(
            "Empty-stem energy probe failed for %r - %r (%s): %s; treating "
            "as non-empty and proceeding with the extracted stem",
            entry.artist,
            entry.title,
            drum_wav,
            exc,
        )
        stem_is_empty = False

    if stem_is_empty:
        logger.info(
            "Drum stem is empty/ambient for %r - %r (%s); falling back to "
            "original Master audio",
            entry.artist,
            entry.title,
            stem_path,
        )
        drum_wav.unlink(missing_ok=True)
        return fallback_path, None

    logger.info(
        "Using isolated Drums/Rhythm stem for %r - %r: %s",
        entry.artist,
        entry.title,
        stem_path,
    )
    return drum_wav, drum_wav


def _window_rms(path: str | Path, offset_sec: float, duration_sec: float) -> float:
    """Decode one small window and return its RMS energy, or ``0.0`` on failure.

    A thin convenience wrapper around ``audio.loader.load_window`` used by
    ``_classify_events_against_master`` (spec section 10.2) -- callers
    always want a scalar energy value, never the raw samples.
    """
    try:
        y, _sr = load_window(path, offset_sec=offset_sec, duration_sec=duration_sec)
    except Exception as exc:
        logger.warning(
            "Failed to decode window at %.3fs from %s: %s", offset_sec, path, exc
        )
        return 0.0
    if y.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(y))))


def _classify_events_against_master(
    events: list[DetectedEvent],
    drum_stem_path: str | Path,
    master_path: str | Path,
) -> dict[float, str]:
    """Classify each event as a rhythm-driven "Drop" or melodic "Breakdown".

    Implements the "Smart Validation Gating" step of v2.2 Multi-Source
    Validation (spec section 10.2): for each confirmed event, decode a
    small (``_SMART_VALIDATION_WINDOW_SEC``-second) window centered on its
    timestamp from both the isolated drum stem and the original Master
    audio, then compare their energy:

    - Drum energy high AND Master energy high -> ``"Drop (Rhythm)"``.
    - Drum energy low/zero BUT Master energy high -> ``"Breakdown (Melodic)"``.
    - Otherwise (both low, or drum high but Master low -- which should not
      happen since the drum stem is a subset of the mix) -- no override;
      the event keeps its default "Cue" name.

    Only ever does targeted micro-reads of the Master file via
    ``audio.loader.load_window`` (never a full decode), keeping this pass
    cheap even with ``config.max_cues`` candidates.

    Returns:
        A mapping of ``event.time_ms -> classification label``, containing
        only the events that received an override.
    """
    half_window = _SMART_VALIDATION_WINDOW_SEC / 2.0
    labels: dict[float, str] = {}

    for event in events:
        center_sec = event.time_ms / 1000.0
        offset_sec = max(0.0, center_sec - half_window)

        drum_rms = _window_rms(drum_stem_path, offset_sec, _SMART_VALIDATION_WINDOW_SEC)
        master_rms = _window_rms(master_path, offset_sec, _SMART_VALIDATION_WINDOW_SEC)

        drum_high = drum_rms >= _SMART_HIGH_ENERGY_RMS_THRESHOLD
        master_high = master_rms >= _SMART_HIGH_ENERGY_RMS_THRESHOLD

        if drum_high and master_high:
            labels[event.time_ms] = _LABEL_DROP_RHYTHM
        elif not drum_high and master_high:
            labels[event.time_ms] = _LABEL_BREAKDOWN_MELODIC

        logger.info(
            "Smart validation t=%.3fms drum_rms=%.6f master_rms=%.6f -> %s",
            event.time_ms,
            drum_rms,
            master_rms,
            labels.get(event.time_ms, "(unclassified)"),
        )

    return labels


def _apply_smart_classification(
    events: list[DetectedEvent],
    new_cues: list[CuePoint],
    analysis_path: str | Path,
    temp_stem_wav: Path | None,
    master_path: str | Path,
    config: AppConfig,
) -> None:
    """Relabel ``new_cues`` in place per Smart Validation Gating (spec 10.2).

    A no-op unless ``config.verify == "smart"`` *and* a real drum stem was
    used for analysis (``temp_stem_wav is not None``) -- with no isolated
    stem there is nothing to cross-check the Master against.
    """
    if config.verify != "smart" or temp_stem_wav is None:
        return

    labels = _classify_events_against_master(events, analysis_path, master_path)
    if not labels:
        return

    for cue in new_cues:
        label = labels.get(cue.start_ms)
        if label is not None:
            cue.name = label


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

    v2.0: if the matched entry has a native stem sidecar available (spec
    section 9), the isolated Drums/Rhythm stem is analyzed instead of
    ``track_path`` -- see ``_resolve_analysis_source``. This never
    affects which ``<ENTRY>``/``LOCATION`` is matched or written to.

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

    analysis_path, temp_stem_wav = _resolve_analysis_source(
        entry,
        track_path,
        nml_path,
        stems_dir=config.stems_dir,
        no_stems=config.no_stems,
    )
    try:
        events = detect_events(
            audio_path=analysis_path,
            bpm=entry.tempo.bpm,
            grid_anchor_ms=entry.grid_anchor_ms,
            duration_ms=entry.duration_ms,
            config=config,
            track_title=f"{entry.artist} - {entry.title}",
            peak_db=entry.peak_db,
            perceived_db=entry.perceived_db,
        )

        logger.info("Detected %d event(s)", len(events))

        new_cues = map_events_to_cues(events, entry.cues, clear_existing=clear_existing)
        logger.info("Mapped %d event(s) to free HOTCUE slots", len(new_cues))

        # v2.2 Smart Validation Gating (spec section 10.2): relabel cues as
        # "Drop (Rhythm)"/"Breakdown (Melodic)" while the temp stem WAV is
        # still on disk -- must happen before the finally block deletes it.
        _apply_smart_classification(
            events, new_cues, analysis_path, temp_stem_wav, track_path, config
        )
    finally:
        if temp_stem_wav is not None:
            temp_stem_wav.unlink(missing_ok=True)

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

    v2.0: each track's analysis source is resolved the same way as
    ``run_pipeline`` -- see ``_resolve_analysis_source`` -- so batches
    transparently benefit from native stems where available, with a
    graceful per-track fallback to the original audio otherwise.

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
    else:  # track_title is not None
        batch_refs = parser.find_entries_by_title(track_title, artist=artist)

    logger.info("Resolved %d track(s) for batch processing", len(batch_refs))

    total_tracks = len(batch_refs)

    # Process each track
    writer = NmlWriter(parser)
    results: list[BatchTrackResult] = []

    for i, batch_ref in enumerate(batch_refs, 1):
        entry = batch_ref.entry
        element = batch_ref.element

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

        # v2.0/v2.1: resolve stem vs. original audio source before detection.
        analysis_path, temp_stem_wav = _resolve_analysis_source(
            entry, entry.location_path, nml_path, stems_dir=config.stems_dir
        )

        # Detection with broad error handling (spec section 8.3, step 2)
        events: list[DetectedEvent] | None = None
        new_cues: list[CuePoint] = []
        try:
            try:
                events = detect_events(
                    audio_path=analysis_path,
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

            logger.info(
                "Detected %d event(s) in %r - %r",
                len(events),
                entry.artist,
                entry.title,
            )

            # Map and write (spec section 8.3, steps 3-4)
            new_cues = map_events_to_cues(
                events, entry.cues, clear_existing=clear_existing
            )
            logger.info("Mapped %d event(s) to free HOTCUE slots", len(new_cues))

            # v2.2 Smart Validation Gating (spec section 10.2): relabel cues
            # while the temp stem WAV is still on disk -- must happen before
            # the finally block deletes it.
            _apply_smart_classification(
                events,
                new_cues,
                analysis_path,
                temp_stem_wav,
                entry.location_path,
                config,
            )
        finally:
            if temp_stem_wav is not None:
                temp_stem_wav.unlink(missing_ok=True)

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
