"""Grid-Guided Phrase Analysis orchestrator.

Implements the ``audio.detector`` responsibility from
``.openspec/2-spec.md`` section 2.1, and the full algorithm from section
6.1: for each phrase candidate produced by ``audio.beatgrid``, decode small
before/after windows via ``audio.loader``, extract RMS/MFCC features with
``librosa``, score the change via ``audio.features``, and confirm
significant candidates as a single, unified pool of ``DetectedEvent``s
(no position-based intro/drop/outro roles -- see spec section 6.1, v1.4).

This module never analyzes anything outside a candidate's window and never
touches XML (spec section 2.1).
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, replace
from pathlib import Path

import librosa
import numpy as np

from cuegrid.audio.beatgrid import (
    PhraseCandidate,
    beat_length_ms,
    generate_phrase_candidates,
)
from cuegrid.audio.features import ScoreResult, score_candidate
from cuegrid.audio.loader import load_window
from cuegrid.config import AppConfig
from cuegrid.telemetry import TELEMETRY_FIELDNAMES, append_telemetry_rows

logger = logging.getLogger(__name__)

# Anti-silence filter (spec section 6.1, step 5): a candidate is never
# significant if its "after" window is practically silent -- either at an
# absolute near-zero floor, or extremely low relative to the track's
# average energy across all sampled windows. This is what stops a phrase
# boundary sitting inside a fade-out from being scored as a big (but
# spurious) energy change.
_SILENCE_ABS_RMS_FLOOR = 1e-4
_SILENCE_RELATIVE_FRACTION = 0.1  # 10% of the track's average sampled RMS


@dataclass
class DetectedEvent:
    """A confirmed structural event (spec section 2.3)."""

    label: str  # always "cue" -- a single, unified structural cue type
    time_ms: float  # == the confirming PhraseCandidate.time_ms; already grid-exact
    beat_index: int  # traceability back to the originating PhraseCandidate
    is_major_phrase: bool  # carried through from the originating PhraseCandidate
    confidence: float  # combined energy/timbre change score, arbitrary positive scale


@dataclass
class _ScoredCandidate:
    """Internal: a ``PhraseCandidate`` plus its (possibly missing) score."""

    candidate: PhraseCandidate
    score: ScoreResult | None
    rms_after: float | None = None


# Explicit CLI export uses the same locked schema as the internal cache.
_CSV_FIELDNAMES = TELEMETRY_FIELDNAMES


def _telemetry_row(
    track_title: str,
    candidate: PhraseCandidate,
    score: ScoreResult | None,
    status: str,
    peak_db: float | None,
    perceived_db: float | None,
) -> dict[str, str | int | float]:
    return {
        "track_title": track_title,
        "beat": candidate.beat_index,
        "time_ms": f"{candidate.time_ms:.3f}",
        "energy_delta_db": f"{score.energy_delta_db:.3f}" if score is not None else "",
        "timbre_dist": f"{score.timbre_distance:.3f}" if score is not None else "",
        "confidence": f"{score.confidence:.3f}" if score is not None else "",
        "status": status,
        "track_peak_db": f"{peak_db:.6f}" if peak_db is not None else "",
        "track_perceived_db": f"{perceived_db:.6f}" if perceived_db is not None else "",
    }


def _write_csv_row(writer: csv.DictWriter, row: dict[str, str | int | float]) -> None:
    writer.writerow(row)


def _extract_rms_mfcc(
    y: np.ndarray, sr: int, config: AppConfig
) -> tuple[float, np.ndarray]:
    """Extract mean RMS energy and mean MFCC vector for a decoded window."""
    rms = float(librosa.feature.rms(y=y, hop_length=config.hop_length)[0].mean())
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=config.mfcc_count).mean(axis=1)
    return rms, mfcc


def _is_silent(rms_after: float, track_avg_rms: float) -> bool:
    """Anti-silence filter (spec section 6.1, step 5).

    ``True`` if ``rms_after`` is practically at zero, or extremely low
    compared to ``track_avg_rms`` (the mean RMS observed across every
    sampled window on this track) -- i.e. this candidate's "after" window
    sits inside a fade-out or other near-silent passage, and must never be
    confirmed as significant regardless of how large its energy-delta or
    timbre-distance scores look on paper.
    """
    if rms_after <= _SILENCE_ABS_RMS_FLOOR:
        return True
    return track_avg_rms > 0 and rms_after < track_avg_rms * _SILENCE_RELATIVE_FRACTION


def _score_candidates(
    audio_path: str | Path,
    candidates: list[PhraseCandidate],
    bpm: float,
    duration_ms: float,
    config: AppConfig,
) -> tuple[list[_ScoredCandidate], dict[int, str]]:
    """Decode before/after windows for each candidate and score them.

    Implements spec section 6.1, steps 2-5, including the anti-silence
    filter that rejects candidates whose "after" window is practically
    silent relative to the track's average sampled energy.

    Returns:
        A tuple of ``(scored_candidates, status_by_beat_index)`` where
        ``status_by_beat_index`` records the rejection reason for every
        candidate that did not pass scoring.
    """
    window_ms = config.window_beats * beat_length_ms(bpm)

    logger.debug(
        "Scoring %d candidate(s) with window_ms=%.3f (window_beats=%.2f, bpm=%.3f)",
        len(candidates),
        window_ms,
        config.window_beats,
        bpm,
    )

    # Pass 1: decode + extract raw features for every candidate, without
    # scoring yet -- we need the track-wide average RMS first (anti-silence
    # filter needs it as a baseline). ``before``/``after`` are each either
    # ``None`` or a ``(rms, mfcc)`` pair, kept together so type-narrowing on
    # one item also narrows the other.
    raw: list[
        tuple[
            PhraseCandidate,
            tuple[float, np.ndarray] | None,
            tuple[float, np.ndarray] | None,
        ]
    ] = []
    all_rms_samples: list[float] = []

    for candidate in candidates:
        before_offset_ms = candidate.time_ms - window_ms
        has_before = before_offset_ms >= 0

        after_duration_ms = min(window_ms, duration_ms - candidate.time_ms)
        has_after = after_duration_ms > 0

        before = None
        if has_before:
            y_before, sr_before = load_window(
                audio_path,
                offset_sec=before_offset_ms / 1000.0,
                duration_sec=window_ms / 1000.0,
                sr=config.sample_rate,
            )
            before = _extract_rms_mfcc(y_before, sr_before, config)
            all_rms_samples.append(before[0])

        after = None
        if has_after:
            y_after, sr_after = load_window(
                audio_path,
                offset_sec=candidate.time_ms / 1000.0,
                duration_sec=after_duration_ms / 1000.0,
                sr=config.sample_rate,
            )
            after = _extract_rms_mfcc(y_after, sr_after, config)
            all_rms_samples.append(after[0])

        raw.append((candidate, before, after))

    track_avg_rms = float(np.mean(all_rms_samples)) if all_rms_samples else 0.0

    # Pass 2: score each candidate now that the track-wide average RMS is
    # known, applying the anti-silence filter on top of the base score.
    scored: list[_ScoredCandidate] = []
    status_by_beat: dict[int, str] = {}
    for candidate, before, after in raw:
        score = None
        rms_after = after[0] if after is not None else None
        if before is not None and after is not None:
            rms_before, mfcc_before = before
            rms_after, mfcc_after = after
            score = score_candidate(
                rms_before, rms_after, mfcc_before, mfcc_after, config
            )
            if score.is_significant and _is_silent(rms_after, track_avg_rms):
                logger.info(
                    "Candidate beat=%d t=%.3fms rms_after=%.6f "
                    "(track_avg=%.6f) -> ANTI-SILENCE OVERRIDE (was significant)",
                    candidate.beat_index,
                    candidate.time_ms,
                    rms_after,
                    track_avg_rms,
                )
                score = replace(score, is_significant=False)
                status_by_beat[candidate.beat_index] = "REJECTED_SILENCE"

        if score is not None:
            if score.is_significant:
                decision = "SIGNIFICANT"
            else:
                decision = "REJECTED"
                # Only set REJECTED_THRESHOLD if not already marked as silence
                if candidate.beat_index not in status_by_beat:
                    status_by_beat[candidate.beat_index] = "REJECTED_THRESHOLD"
            logger.info(
                "Candidate beat=%d t=%.3fms energy_delta_db=%.3f "
                "timbre_distance=%.3f -> %s",
                candidate.beat_index,
                candidate.time_ms,
                score.energy_delta_db,
                score.timbre_distance,
                decision,
            )
        else:
            logger.info(
                "Candidate beat=%d t=%.3fms -> REJECTED (missing %s window)",
                candidate.beat_index,
                candidate.time_ms,
                "before" if before is None else "after",
            )
            status_by_beat[candidate.beat_index] = "REJECTED_MISSING_WINDOW"

        scored.append(
            _ScoredCandidate(candidate=candidate, score=score, rms_after=rms_after)
        )

    return scored, status_by_beat


def _select_cues(
    scored: list[_ScoredCandidate],
    config: AppConfig,
    bpm: float,
    duration_ms: float,
) -> tuple[list[DetectedEvent], dict[int, str]]:
    """Select the unified structural-cue pool (spec section 6.1, step 6).

    All significant candidates form a single pool -- there are no more
    position-based intro/drop/outro roles. A dynamic confidence threshold
    (relative to the track's own strongest candidate) filters out weak
    candidates, then the top ``config.max_cues`` (by confidence, ties
    broken in favor of ``is_major_phrase``) are kept and returned in
    chronological order.

    v1.10 outro guard: any candidate whose timestamp falls within the
    last 8 beats of the track is rejected outright, preventing markers
    at the silent or noise-only tail-end of a track.

    Returns:
        A tuple of ``(selected_events, status_by_beat_index)`` where
        ``status_by_beat_index`` maps each scored candidate's beat index
        to its final status string (``SELECTED``, ``DISCARDED_LIMIT``,
        etc.) for CSV export.
    """
    status_by_beat: dict[int, str] = {}

    # v1.10 outro guard: compute the earliest timestamp that is considered
    # "too close to the end" (within the last 8 beats).
    _OUTRO_GUARD_BEATS = 8
    outro_guard_ms = duration_ms - _OUTRO_GUARD_BEATS * beat_length_ms(bpm)

    significant: list[tuple[PhraseCandidate, ScoreResult]] = []
    for sc in scored:
        if sc.score is None or not sc.score.is_significant:
            continue
        if sc.candidate.time_ms >= outro_guard_ms:
            logger.info(
                "Candidate beat=%d t=%.3fms -> REJECTED_OUTRO_GUARD "
                "(within last %d beats, outro_guard_ms=%.3f)",
                sc.candidate.beat_index,
                sc.candidate.time_ms,
                _OUTRO_GUARD_BEATS,
                outro_guard_ms,
            )
            status_by_beat[sc.candidate.beat_index] = "REJECTED_OUTRO_GUARD"
            continue
        significant.append((sc.candidate, sc.score))
    if not significant:
        return [], status_by_beat

    max_confidence = max(score.confidence for _, score in significant)
    confidence_floor = max_confidence * config.relative_confidence_threshold

    survivors = [
        (candidate, score)
        for candidate, score in significant
        if score.confidence >= confidence_floor
    ]
    discarded = [
        (candidate, score)
        for candidate, score in significant
        if score.confidence < confidence_floor
    ]
    for candidate, score in discarded:
        logger.info(
            "Candidate DISCARDED beat=%d t=%.3fms confidence=%.3f "
            "(below relative threshold=%.3f of max_confidence=%.3f)",
            candidate.beat_index,
            candidate.time_ms,
            score.confidence,
            confidence_floor,
            max_confidence,
        )
        status_by_beat[candidate.beat_index] = "DISCARDED_LIMIT"

    # Descending confidence; ties broken in favor of is_major_phrase.
    survivors.sort(
        key=lambda pair: (pair[1].confidence, pair[0].is_major_phrase), reverse=True
    )
    selected = survivors[: config.max_cues]
    excluded = survivors[config.max_cues :]
    for candidate, score in excluded:
        logger.info(
            "Candidate DISCARDED beat=%d t=%.3fms confidence=%.3f "
            "(exceeds max_cues=%d)",
            candidate.beat_index,
            candidate.time_ms,
            score.confidence,
            config.max_cues,
        )
        status_by_beat[candidate.beat_index] = "DISCARDED_LIMIT"

    for candidate, _score in selected:
        status_by_beat[candidate.beat_index] = "SELECTED"

    events = [
        DetectedEvent(
            label="cue",
            time_ms=candidate.time_ms,
            beat_index=candidate.beat_index,
            is_major_phrase=candidate.is_major_phrase,
            confidence=score.confidence,
        )
        for candidate, score in selected
    ]
    events.sort(key=lambda e: e.time_ms)
    return events, status_by_beat


def detect_events(
    audio_path: str | Path,
    bpm: float,
    grid_anchor_ms: float,
    duration_ms: float,
    config: AppConfig | None = None,
    track_title: str = "",
    peak_db: float | None = None,
    perceived_db: float | None = None,
) -> list[DetectedEvent]:
    """Run Grid-Guided Phrase Analysis end-to-end for one track.

    Implements ``.openspec/2-spec.md`` section 6.1 in full: generates
    phrase candidates (section 4), decodes and scores small windows around
    each one, and returns the confirmed ``DetectedEvent`` list in
    chronological order. No snapping/de-duplication pass follows this --
    every event's ``time_ms`` is already grid-exact (spec section 6.1,
    step 7).

    If ``config.export_csv_path`` is set, per-candidate telemetry is
    appended to that CSV file (v1.8 data export).

    Args:
        audio_path: Path to the audio file to analyze.
        bpm: Track tempo, from ``<TEMPO BPM="...">``.
        grid_anchor_ms: Grid anchor (beat 0), in milliseconds -- from the
            ``<CUE_V2 TYPE="4">`` (``AutoGrid``) element.
        duration_ms: Track duration, in milliseconds -- from ``<INFO>``
            (see spec section 2.3's ``PLAYTIME_FLOAT``/``PLAYTIME`` rule).
        config: Tunable thresholds; defaults to ``AppConfig()``.
        track_title: Human-readable track identifier for CSV export rows.
        peak_db: Track peak loudness from ``<LOUDNESS PEAK_DB="...">``
            (v1.9); ``None`` if the NML had no ``<LOUDNESS>`` element.
        perceived_db: Track perceived loudness from
            ``<LOUDNESS PERCEIVED_DB="...">`` (v1.9).

    Returns:
        The confirmed ``DetectedEvent`` list, in chronological order. Empty
        if no phrase candidates could be generated (spec section 4.4) or
        none were confirmed as significant.
    """
    config = config or AppConfig()

    candidates = generate_phrase_candidates(
        bpm=bpm,
        grid_anchor_ms=grid_anchor_ms,
        duration_ms=duration_ms,
        phrase_beats=config.phrase_beats,
        major_phrase_multiple=config.major_phrase_multiple,
    )
    if not candidates:
        return []

    scored, score_status = _score_candidates(
        audio_path, candidates, bpm, duration_ms, config
    )
    events, select_status = _select_cues(scored, config, bpm, duration_ms)

    # Merge status dicts: _select_cues statuses (SELECTED, DISCARDED_LIMIT)
    # take precedence over _score_candidates statuses (REJECTED_*).
    all_status = {**score_status, **select_status}

    rows = [
        _telemetry_row(
            track_title,
            sc.candidate,
            sc.score,
            all_status.get(sc.candidate.beat_index, "REJECTED_THRESHOLD"),
            peak_db,
            perceived_db,
        )
        for sc in scored
    ]
    append_telemetry_rows(rows)

    # Preserve the explicit CLI export path as a separate, user-requested
    # output while keeping the internal cache fixed and run-scoped.
    if config.export_csv_path is not None:
        _export_csv(config.export_csv_path, rows)

    return events


def _export_csv(csv_path: str, rows: list[dict[str, str | int | float]]) -> None:
    """Append rows to the explicit user-requested CSV export path."""
    file_exists = Path(csv_path).exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            _write_csv_row(writer, row)
    logger.info("Exported %d candidate row(s) to %s", len(rows), csv_path)
