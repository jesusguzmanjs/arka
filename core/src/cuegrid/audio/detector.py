"""Grid-guided, windowed HPSS phrase analysis."""

from __future__ import annotations

import csv
import gc
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path

import librosa
import numpy as np

from cuegrid.audio.beatgrid import PhraseCandidate, beat_length_ms, generate_phrase_candidates
from cuegrid.audio.features import ScoreResult, score_candidate
from cuegrid.config import AppConfig
from cuegrid.telemetry import TELEMETRY_FIELDNAMES, append_telemetry_rows, format_timestamp_ms

logger = logging.getLogger(__name__)

_SILENCE_ABS_RMS_FLOOR = 1e-4
_SILENCE_RELATIVE_FRACTION = 0.1
_OUTRO_GUARD_BEATS = 8

@dataclass
class DetectedEvent:
    label: str
    time_ms: float
    beat_index: int
    is_major_phrase: bool
    confidence: float


@dataclass
class _ScoredCandidate:
    candidate: PhraseCandidate
    score: ScoreResult | None
    harmonic_after: float | None = None
    percussive_after: float | None = None


@dataclass
class _PerformanceMetrics:
    """Execution metrics collected while scoring one track's candidates."""

    audio_decode_seconds: float = 0.0
    dsp_extraction_seconds: float = 0.0
    successful_windows: int = 0
    rejected_windows: int = 0
    successfully_scored_candidates: int = 0


def _extract_hpss_features(
        y: np.ndarray, sr: int, config: AppConfig
) -> tuple[float, float, np.ndarray]:
    """Return mean harmonic RMS, percussive RMS, and MFCCs for one window."""

    # 1. Creamos el espectrograma a mitad de resolución (n_fft=1024)
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=config.hop_length))

    # 2. Separamos armónicos y percusión
    harmonic, percussive = librosa.decompose.hpss(S, kernel_size=15)

    # 3. Calculamos la energía indicando explícitamente el tamaño de la ventana (1024)
    harmonic_rms = float(librosa.feature.rms(S=harmonic, frame_length=1024)[0].mean())
    percussive_rms = float(librosa.feature.rms(S=percussive, frame_length=1024)[0].mean())

    # 4. El MFCC sigue igual
    mfcc_mean = np.asarray(
        librosa.feature.mfcc(y=y, sr=sr, n_mfcc=config.mfcc_count).mean(axis=1),
        dtype=np.float64,
    )

    if not (
            np.isfinite(harmonic_rms)
            and np.isfinite(percussive_rms)
            and np.all(np.isfinite(mfcc_mean))
    ):
        raise ValueError("HPSS feature extraction produced non-finite values")

    return harmonic_rms, percussive_rms, mfcc_mean

def _is_silent(percussive_after: float, track_avg_percussive_rms: float) -> bool:
    if percussive_after <= _SILENCE_ABS_RMS_FLOOR:
        return True
    return (
        track_avg_percussive_rms > 0
        and percussive_after < track_avg_percussive_rms * _SILENCE_RELATIVE_FRACTION
    )


def _score_candidates(
    audio_path: str | Path,
    candidates: list[PhraseCandidate],
    bpm: float,
    duration_ms: float,
    config: AppConfig,
) -> tuple[list[_ScoredCandidate], dict[int, str], _PerformanceMetrics]:
    """Decode the track once, extract HPSS from candidate windows, then score it.

    Timing is accumulated around the full-track decode and feature extraction
    calls so the logger can distinguish I/O cost from DSP cost.
    Candidate-level counts treat edge-margin and unscorable candidates as
    rejected, while a candidate is successful only when both windows were
    extracted and it produced a score.
    """
    window_ms = config.window_beats * beat_length_ms(bpm)
    outro_guard_ms = duration_ms - _OUTRO_GUARD_BEATS * beat_length_ms(bpm)
    raw: list[
        tuple[
            PhraseCandidate,
            tuple[float, float, np.ndarray] | None,
            tuple[float, float, np.ndarray] | None,
        ]
    ] = []
    percussive_samples: list[float] = []
    status_by_beat: dict[int, str] = {}
    metrics = _PerformanceMetrics()

    decode_started = time.perf_counter()
    try:
        full_y, full_sr = librosa.load(str(audio_path), sr=config.sample_rate, mono=True)
    finally:
        metrics.audio_decode_seconds += time.perf_counter() - decode_started

    try:
        for candidate in candidates:
            if candidate.beat_index < 8 or candidate.time_ms >= outro_guard_ms:
                status_by_beat[candidate.beat_index] = "REJECTED_EDGE_MARGIN"
                metrics.rejected_windows += 1
                continue

            before_offset_ms = candidate.time_ms - window_ms
            after_duration_ms = min(window_ms, duration_ms - candidate.time_ms)
            before = after = None
            try:
                if before_offset_ms >= 0:
                    before_start = max(0, int((before_offset_ms / 1000.0) * full_sr))
                    before_end = min(len(full_y), int((candidate.time_ms / 1000.0) * full_sr))
                    before_y = full_y[before_start:before_end]

                    dsp_started = time.perf_counter()
                    try:
                        before = _extract_hpss_features(before_y, full_sr, config)
                    finally:
                        metrics.dsp_extraction_seconds += time.perf_counter() - dsp_started
                if after_duration_ms > 0:
                    after_start = max(0, int((candidate.time_ms / 1000.0) * full_sr))
                    after_end = min(
                        len(full_y),
                        int(((candidate.time_ms + after_duration_ms) / 1000.0) * full_sr),
                    )
                    after_y = full_y[after_start:after_end]

                    dsp_started = time.perf_counter()
                    try:
                        after = _extract_hpss_features(after_y, full_sr, config)
                    finally:
                        metrics.dsp_extraction_seconds += time.perf_counter() - dsp_started
                    percussive_samples.append(after[1])
            except Exception as exc:
                logger.warning("Candidate beat=%d HPSS extraction failed: %s", candidate.beat_index, exc)
                status_by_beat[candidate.beat_index] = "REJECTED_UNSCORABLE"
                metrics.rejected_windows += 1
                raw.append((candidate, None, None))
                continue

            if before is not None and after is not None:
                metrics.successful_windows += 1
            else:
                metrics.rejected_windows += 1
            raw.append((candidate, before, after))
    finally:
        del full_y
        gc.collect()

    track_avg_percussive = float(np.mean(percussive_samples)) if percussive_samples else 0.0
    scored: list[_ScoredCandidate] = []
    for candidate, before, after in raw:
        score = None
        harmonic_after = percussive_after = None
        if before is not None and after is not None:
            harmonic_before, percussive_before, mfcc_before = before
            harmonic_after, percussive_after, mfcc_after = after
            score = score_candidate(harmonic_before, harmonic_after, percussive_before, percussive_after, mfcc_before, mfcc_after, config)
            metrics.successfully_scored_candidates += 1
            if score.is_significant and _is_silent(percussive_after, track_avg_percussive):
                score = replace(score, is_significant=False)
                status_by_beat[candidate.beat_index] = "REJECTED_SILENCE"
        if score is None:
            status_by_beat.setdefault(candidate.beat_index, "REJECTED_MISSING_WINDOW")
        elif score.is_significant:
            logger.info("Candidate beat=%d t=%.3fms structural_delta_db=%.3f -> SIGNIFICANT", candidate.beat_index, candidate.time_ms, score.energy_delta_db)
        else:
            status_by_beat.setdefault(candidate.beat_index, "REJECTED_THRESHOLD")
        scored.append(_ScoredCandidate(candidate, score, harmonic_after, percussive_after))
    return scored, status_by_beat, metrics


def _select_cues(scored: list[_ScoredCandidate], config: AppConfig, bpm: float, duration_ms: float) -> tuple[list[DetectedEvent], dict[int, str]]:
    """Apply the relative threshold and HPSS spatial soft guardrail."""
    del bpm  # Edge guards are enforced before audio decoding in _score_candidates.
    status_by_beat: dict[int, str] = {}
    weighted: list[tuple[PhraseCandidate, ScoreResult, float, float]] = []
    for sc in scored:
        if sc.score is None or not sc.score.is_significant:
            continue
        x = float(np.clip(sc.candidate.time_ms / duration_ms, 0.0, 1.0)) if duration_ms > 0 else 0.0
        spatial_weight = 1.0 - config.spatial_penalty_alpha * ((2.0 * x - 1.0) ** 2)
        weighted.append((sc.candidate, sc.score, spatial_weight, sc.score.confidence * spatial_weight))
    if not weighted:
        return [], status_by_beat

    max_confidence = max(final for _, _, _, final in weighted)
    floor = max_confidence * config.relative_confidence_threshold
    survivors = [item for item in weighted if item[3] >= floor]
    for candidate, _, _, final in weighted:
        if final < floor:
            status_by_beat[candidate.beat_index] = "DISCARDED_LIMIT"
    survivors.sort(key=lambda item: item[3], reverse=True)
    selected = survivors[: config.max_cues]
    for candidate, _, _, _ in survivors[config.max_cues:]:
        status_by_beat[candidate.beat_index] = "DISCARDED_LIMIT"
    for candidate, _, _, _ in selected:
        status_by_beat[candidate.beat_index] = "SELECTED"

    events = [DetectedEvent("cue", candidate.time_ms, candidate.beat_index, candidate.is_major_phrase, final) for candidate, _, _, final in selected]
    events.sort(key=lambda event: event.time_ms)
    return events, status_by_beat


def _telemetry_row(track_title: str, candidate: PhraseCandidate, score: ScoreResult | None, status: str, peak_db: float | None, perceived_db: float | None, duration_ms: float, alpha: float) -> dict[str, str | int | float]:
    x = float(np.clip(candidate.time_ms / duration_ms, 0.0, 1.0)) if duration_ms > 0 else 0.0
    spatial_weight = 1.0 - alpha * ((2.0 * x - 1.0) ** 2)
    original = score.confidence if score is not None else None
    return {"track_title": track_title, "Formatted_Time": format_timestamp_ms(candidate.time_ms), "beat": candidate.beat_index, "time_ms": f"{candidate.time_ms:.3f}", "energy_delta_db": f"{score.energy_delta_db:.3f}" if score else "", "harmonic_delta_db": f"{score.harmonic_delta_db:.3f}" if score else "", "percussive_delta_db": f"{score.percussive_delta_db:.3f}" if score else "", "timbre_dist": f"{score.timbre_distance:.3f}" if score else "", "original_confidence": f"{original:.3f}" if original is not None else "", "spatial_weight": f"{spatial_weight:.3f}" if score else "", "confidence": f"{original * spatial_weight:.3f}" if original is not None else "", "status": status, "track_peak_db": f"{peak_db:.6f}" if peak_db is not None else "", "track_perceived_db": f"{perceived_db:.6f}" if perceived_db is not None else ""}


def detect_events(audio_path: str | Path, bpm: float, grid_anchor_ms: float, duration_ms: float, config: AppConfig | None = None, track_title: str = "", peak_db: float | None = None, perceived_db: float | None = None) -> list[DetectedEvent]:
    """Run targeted master-track HPSS analysis and return chronological cues."""
    detection_started = time.perf_counter()
    config = config or AppConfig()
    candidates = generate_phrase_candidates(bpm, grid_anchor_ms, duration_ms, config.phrase_beats, config.major_phrase_multiple)
    metrics = _PerformanceMetrics()

    if candidates:
        scored, score_status, metrics = _score_candidates(
            audio_path, candidates, bpm, duration_ms, config
        )
        events, select_status = _select_cues(scored, config, bpm, duration_ms)
        all_status = {**score_status, **select_status}
        rows = [
            _telemetry_row(
                track_title,
                sc.candidate,
                sc.score,
                all_status.get(sc.candidate.beat_index, "REJECTED_THRESHOLD"),
                peak_db,
                perceived_db,
                duration_ms,
                config.spatial_penalty_alpha,
            )
            for sc in scored
        ]
        append_telemetry_rows(rows)
        if config.export_csv_path is not None:
            _export_csv(config.export_csv_path, rows)
    else:
        events = []

    overall_seconds = time.perf_counter() - detection_started
    logger.info(
        "Detection performance summary for track %r: "
        "Total candidates generated=%d; "
        "Total candidates successfully decoded and scored=%d; "
        "Successful windows=%d; Rejected windows=%d; "
        "Total audio decode time=%.6f seconds; "
        "Total DSP/HPSS extraction time=%.6f seconds; "
        "Total overall detection time=%.6f seconds",
        track_title,
        len(candidates),
        metrics.successfully_scored_candidates,
        metrics.successful_windows,
        metrics.rejected_windows,
        metrics.audio_decode_seconds,
        metrics.dsp_extraction_seconds,
        overall_seconds,
    )
    return events


def _export_csv(csv_path: str, rows: list[dict[str, str | int | float]]) -> None:
    file_exists = Path(csv_path).exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TELEMETRY_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
