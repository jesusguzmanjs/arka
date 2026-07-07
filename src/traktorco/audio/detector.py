"""Grid-Guided Phrase Analysis orchestrator.

Implements the ``audio.detector`` responsibility from
``.openspec/2-spec.md`` section 2.1, and the full algorithm from section
6.1: for each phrase candidate produced by ``audio.beatgrid``, decode small
before/after windows via ``audio.loader``, extract RMS/MFCC features with
``librosa``, score the change via ``audio.features``, and confirm + label
significant candidates as ``DetectedEvent``s.

This module never analyzes anything outside a candidate's window and never
touches XML (spec section 2.1).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from traktorco.audio.beatgrid import (
    PhraseCandidate,
    beat_length_ms,
    generate_phrase_candidates,
)
from traktorco.audio.features import ScoreResult, score_candidate
from traktorco.audio.loader import load_window
from traktorco.config import AppConfig


@dataclass
class DetectedEvent:
    """A confirmed structural event (spec section 2.3)."""

    label: str  # one of: "intro_end", "drop", "outro_start"
    time_ms: float  # == the confirming PhraseCandidate.time_ms; already grid-exact
    beat_index: int  # traceability back to the originating PhraseCandidate
    is_major_phrase: bool  # carried through from the originating PhraseCandidate
    confidence: float  # combined energy/timbre change score, arbitrary positive scale


@dataclass
class _ScoredCandidate:
    """Internal: a ``PhraseCandidate`` plus its (possibly missing) score."""

    candidate: PhraseCandidate
    score: ScoreResult | None


def _extract_rms_mfcc(
    y: np.ndarray, sr: int, config: AppConfig
) -> tuple[float, np.ndarray]:
    """Extract mean RMS energy and mean MFCC vector for a decoded window."""
    rms = float(librosa.feature.rms(y=y, hop_length=config.hop_length)[0].mean())
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=config.mfcc_count).mean(axis=1)
    return rms, mfcc


def _score_candidates(
    audio_path: str | Path,
    candidates: list[PhraseCandidate],
    bpm: float,
    duration_ms: float,
    config: AppConfig,
) -> list[_ScoredCandidate]:
    """Decode before/after windows for each candidate and score them.

    Implements spec section 6.1, steps 2-5.
    """
    window_ms = config.window_beats * beat_length_ms(bpm)
    scored: list[_ScoredCandidate] = []

    for candidate in candidates:
        before_offset_ms = candidate.time_ms - window_ms
        has_before = before_offset_ms >= 0

        after_duration_ms = min(window_ms, duration_ms - candidate.time_ms)
        has_after = after_duration_ms > 0

        rms_before = mfcc_before = None
        if has_before:
            y_before, sr_before = load_window(
                audio_path,
                offset_sec=before_offset_ms / 1000.0,
                duration_sec=window_ms / 1000.0,
                sr=config.sample_rate,
            )
            rms_before, mfcc_before = _extract_rms_mfcc(y_before, sr_before, config)

        rms_after = mfcc_after = None
        if has_after:
            y_after, sr_after = load_window(
                audio_path,
                offset_sec=candidate.time_ms / 1000.0,
                duration_sec=after_duration_ms / 1000.0,
                sr=config.sample_rate,
            )
            rms_after, mfcc_after = _extract_rms_mfcc(y_after, sr_after, config)

        score = None
        if has_before and has_after:
            score = score_candidate(
                rms_before, rms_after, mfcc_before, mfcc_after, config
            )

        scored.append(_ScoredCandidate(candidate=candidate, score=score))

    return scored


def _pick_boundary_event(
    label: str,
    scored: list[_ScoredCandidate],
    in_window: Callable[[PhraseCandidate], bool],
    pick_earliest: bool,
) -> tuple[_ScoredCandidate | None, DetectedEvent | None]:
    """Shared selection logic for ``intro_end``/``outro_start`` (spec 6.1, step 6).

    Prefers the earliest/latest *significant* candidate in the window;
    falls back to the earliest/latest candidate regardless of
    significance if none are significant (this is how an unscored
    anchor candidate, spec step 5, can still become ``intro_end`` "of
    last resort"). Returns ``(None, None)`` if the window is empty.
    """
    window_candidates = [sc for sc in scored if in_window(sc.candidate)]
    if not window_candidates:
        return None, None

    significant = [
        sc
        for sc in window_candidates
        if sc.score is not None and sc.score.is_significant
    ]
    pool = significant if significant else window_candidates

    chosen = (
        min(pool, key=lambda sc: sc.candidate.beat_index)
        if pick_earliest
        else max(pool, key=lambda sc: sc.candidate.beat_index)
    )
    confidence = chosen.score.confidence if chosen.score is not None else 0.0
    event = DetectedEvent(
        label=label,
        time_ms=chosen.candidate.time_ms,
        beat_index=chosen.candidate.beat_index,
        is_major_phrase=chosen.candidate.is_major_phrase,
        confidence=confidence,
    )
    return chosen, event


def _assign_labels(
    scored: list[_ScoredCandidate], duration_ms: float, config: AppConfig
) -> list[DetectedEvent]:
    """Implements spec section 6.1, step 6."""
    events: list[DetectedEvent] = []
    consumed: set[int] = set()  # beat_index of candidates already used by intro/outro

    intro_cutoff_ms = config.intro_search_fraction * duration_ms
    outro_cutoff_ms = (1.0 - config.outro_search_fraction) * duration_ms

    intro_chosen, intro_event = _pick_boundary_event(
        "intro_end",
        scored,
        in_window=lambda c: c.time_ms <= intro_cutoff_ms,
        pick_earliest=True,
    )
    if intro_event is not None:
        events.append(intro_event)
        consumed.add(intro_chosen.candidate.beat_index)

    outro_chosen, outro_event = _pick_boundary_event(
        "outro_start",
        scored,
        in_window=lambda c: c.time_ms >= outro_cutoff_ms,
        pick_earliest=False,
    )
    if outro_event is not None:
        events.append(outro_event)
        consumed.add(outro_chosen.candidate.beat_index)

    drop_pool = [
        sc
        for sc in scored
        if sc.candidate.beat_index not in consumed
        and sc.score is not None
        and sc.score.is_significant
        and sc.score.energy_delta_db > 0
    ]
    # Descending confidence; ties broken in favor of is_major_phrase (spec 6.1, step 6).
    drop_pool.sort(
        key=lambda sc: (sc.score.confidence, sc.candidate.is_major_phrase), reverse=True
    )
    for sc in drop_pool[: config.max_drop_cues]:
        events.append(
            DetectedEvent(
                label="drop",
                time_ms=sc.candidate.time_ms,
                beat_index=sc.candidate.beat_index,
                is_major_phrase=sc.candidate.is_major_phrase,
                confidence=sc.score.confidence,
            )
        )

    events.sort(key=lambda e: e.time_ms)
    return events


def detect_events(
    audio_path: str | Path,
    bpm: float,
    grid_anchor_ms: float,
    duration_ms: float,
    config: AppConfig | None = None,
) -> list[DetectedEvent]:
    """Run Grid-Guided Phrase Analysis end-to-end for one track.

    Implements ``.openspec/2-spec.md`` section 6.1 in full: generates
    phrase candidates (section 4), decodes and scores small windows around
    each one, and returns the confirmed, labeled ``DetectedEvent`` list in
    chronological order. No snapping/de-duplication pass follows this --
    every event's ``time_ms`` is already grid-exact (spec section 6.1,
    step 7).

    Args:
        audio_path: Path to the audio file to analyze.
        bpm: Track tempo, from ``<TEMPO BPM="...">``.
        grid_anchor_ms: Grid anchor (beat 0), in milliseconds -- from the
            ``<CUE_V2 TYPE="4">`` (``AutoGrid``) element.
        duration_ms: Track duration, in milliseconds -- from ``<INFO>``
            (see spec section 2.3's ``PLAYTIME_FLOAT``/``PLAYTIME`` rule).
        config: Tunable thresholds; defaults to ``AppConfig()``.

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

    scored = _score_candidates(audio_path, candidates, bpm, duration_ms, config)
    return _assign_labels(scored, duration_ms, config)
