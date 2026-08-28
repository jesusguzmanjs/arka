"""Grid-guided, windowed HPSS phrase analysis."""

from __future__ import annotations

import csv
import gc
import librosa
import logging
import numpy as np
import time
from dataclasses import dataclass
from pathlib import Path

from cuegrid.audio.beatgrid import PhraseCandidate, beat_length_ms, generate_phrase_candidates
from cuegrid.audio.features import ScoreResult, score_candidate
from cuegrid.config import AppConfig
from cuegrid.telemetry import TELEMETRY_FIELDNAMES, append_telemetry_rows, format_timestamp_ms

logger = logging.getLogger(__name__)

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

    # 1. Espectrograma a mitad de resolución (n_fft=1024)
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=config.hop_length))

    # 2. Separación armónicos / percusión
    harmonic, percussive = librosa.decompose.hpss(S, kernel_size=15)

    # 3. Energía RMS explícita
    harmonic_rms = float(librosa.feature.rms(S=harmonic, frame_length=1024)[0].mean())
    percussive_rms = float(librosa.feature.rms(S=percussive, frame_length=1024)[0].mean())

    # 4. MFCCs
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


def _score_candidates(
        audio_path: str | Path,
        candidates: list[PhraseCandidate],
        bpm: float,
        duration_ms: float,
        config: AppConfig,
) -> tuple[list[_ScoredCandidate], dict[int, str], _PerformanceMetrics]:
    """Decode the track once, extract HPSS from candidate windows, then score it."""
    window_ms = config.window_beats * beat_length_ms(bpm)
    outro_guard_ms = duration_ms - _OUTRO_GUARD_BEATS * beat_length_ms(bpm)
    raw: list[
        tuple[
            PhraseCandidate,
            tuple[float, float, np.ndarray] | None,
            tuple[float, float, np.ndarray] | None,
        ]
    ] = []
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

    scored: list[_ScoredCandidate] = []
    for candidate, before, after in raw:
        score = None
        harmonic_after = percussive_after = None
        if before is not None and after is not None:
            harmonic_before, percussive_before, mfcc_before = before
            harmonic_after, percussive_after, mfcc_after = after
            score = score_candidate(
                harmonic_before, harmonic_after, percussive_before, percussive_after, mfcc_before, mfcc_after, config
            )
            metrics.successfully_scored_candidates += 1

        if score is None:
            status_by_beat.setdefault(candidate.beat_index, "REJECTED_MISSING_WINDOW")
        elif score.is_significant:
            logger.info("Candidate beat=%d t=%.3fms structural_delta_db=%.3f -> SIGNIFICANT", candidate.beat_index, candidate.time_ms, score.energy_delta_db)
        else:
            status_by_beat.setdefault(candidate.beat_index, "REJECTED_THRESHOLD")
        scored.append(_ScoredCandidate(candidate, score, harmonic_after, percussive_after))
    return scored, status_by_beat, metrics


def _calculate_proximity_weight(candidate_time_ms: float, accepted_times_ms: list[float], bpm: float) -> float:
    """Calcula un factor (0.0 a 1.0) basado en la distancia al cue aceptado más cercano.

    - < 4 beats: 0.0 (anulado)
    - 4 a 32 beats: penalización progresiva cuadrática
    - >= 32 beats: 1.0 (sin penalización)
    """
    if not accepted_times_ms:
        return 1.0

    min_delta_ms = min(abs(candidate_time_ms - t) for t in accepted_times_ms)
    ms_per_beat = beat_length_ms(bpm)
    delta_beats = min_delta_ms / ms_per_beat if ms_per_beat > 0 else 999.0

    if delta_beats < 4.0:
        return 0.0

    if delta_beats >= 32.0:
        return 1.0

    progress = (delta_beats - 4.0) / (32.0 - 4.0)
    return float(progress ** 2)


def _select_cues(
        scored: list[_ScoredCandidate],
        config: AppConfig,
        bpm: float,
        duration_ms: float,
) -> tuple[list[DetectedEvent], dict[int, str]]:
    """Apply spatial plateau, relative threshold, and dynamic proximity suppression."""
    status_by_beat: dict[int, str] = {}
    weighted: list[tuple[PhraseCandidate, ScoreResult, float, float]] = []

    # 1. Ponderación por meseta espacial (40% a 60% libre de penalización)
    for sc in scored:
        if sc.score is None or not sc.score.is_significant:
            continue
        if sc.percussive_after is None or sc.percussive_after <= 1e-4:
            status_by_beat[sc.candidate.beat_index] = "REJECTED_SILENCE"
            continue

        x = float(np.clip(sc.candidate.time_ms / duration_ms, 0.0, 1.0)) if duration_ms > 0 else 0.0

        dist_from_center = abs(x - 0.5)
        plateau_width = 0.20  # Meseta del 40% al 60%
        plateau_radius = plateau_width / 2.0
        falloff_range = 0.5 - plateau_radius

        d_norm = max(0.0, dist_from_center - plateau_radius) / falloff_range
        spatial_weight = 1.0 - config.spatial_penalty_alpha * (d_norm ** 2)

        weighted.append((sc.candidate, sc.score, spatial_weight, sc.score.confidence * spatial_weight))

    if not weighted:
        return [], status_by_beat

    # 2. Suelo de confianza relativa
    max_confidence = max(final for _, _, _, final in weighted)
    floor = max_confidence * config.relative_confidence_threshold
    survivors = [item for item in weighted if item[3] >= floor]

    for candidate, _, _, final in weighted:
        if final < floor:
            status_by_beat[candidate.beat_index] = "DISCARDED_LIMIT"

    # 3. Selección dinámica con penalización por proximidad
    selected_events: list[DetectedEvent] = []
    accepted_times_ms: list[float] = []

    pool = list(survivors)

    while pool and len(selected_events) < config.max_cues:
        recalculated_pool = []
        for candidate, score, spatial_w, base_confidence in pool:
            prox_w = _calculate_proximity_weight(candidate.time_ms, accepted_times_ms, bpm)
            final_conf = base_confidence * prox_w
            recalculated_pool.append((candidate, score, spatial_w, base_confidence, prox_w, final_conf))

        recalculated_pool.sort(key=lambda item: item[5], reverse=True)

        best = recalculated_pool[0]
        best_candidate, best_score, best_spatial_w, best_base_conf, best_prox_w, best_final_conf = best

        if best_final_conf <= 0.0 or best_prox_w == 0.0:
            for cand, _, _, _, _, _ in recalculated_pool:
                status_by_beat[cand.beat_index] = "DISCARDED_TOO_CLOSE"
            break

        accepted_times_ms.append(best_candidate.time_ms)
        status_by_beat[best_candidate.beat_index] = "SELECTED"

        selected_events.append(
            DetectedEvent("cue", best_candidate.time_ms, best_candidate.beat_index, best_candidate.is_major_phrase, best_final_conf)
        )

        pool = [item[:4] for item in recalculated_pool[1:] if item[4] > 0.0]

    for cand, _, _, _ in pool:
        status_by_beat.setdefault(cand.beat_index, "DISCARDED_LIMIT")

    selected_events.sort(key=lambda event: event.time_ms)
    return selected_events, status_by_beat


def _telemetry_row(
        track_title: str,
        candidate: PhraseCandidate,
        score: ScoreResult | None,
        status: str,
        peak_db: float | None,
        perceived_db: float | None,
        duration_ms: float,
        alpha: float,
) -> dict[str, str | int | float]:
    x = float(np.clip(candidate.time_ms / duration_ms, 0.0, 1.0)) if duration_ms > 0 else 0.0

    dist_from_center = abs(x - 0.5)
    plateau_width = 0.20
    plateau_radius = plateau_width / 2.0
    falloff_range = 0.5 - plateau_radius

    d_norm = max(0.0, dist_from_center - plateau_radius) / falloff_range
    spatial_weight = 1.0 - alpha * (d_norm ** 2)

    original = score.confidence if score is not None else None
    return {
        "track_title": track_title,
        "Formatted_Time": format_timestamp_ms(candidate.time_ms),
        "beat": candidate.beat_index,
        "time_ms": f"{candidate.time_ms:.3f}",
        "energy_delta_db": f"{score.energy_delta_db:.3f}" if score else "",
        "harmonic_delta_db": f"{score.harmonic_delta_db:.3f}" if score else "",
        "percussive_delta_db": f"{score.percussive_delta_db:.3f}" if score else "",
        "timbre_dist": f"{score.timbre_distance:.3f}" if score else "",
        "original_confidence": f"{original:.3f}" if original is not None else "",
        "spatial_weight": f"{spatial_weight:.3f}" if score else "",
        "confidence": f"{original * spatial_weight:.3f}" if original is not None else "",
        "status": status,
        "track_peak_db": f"{peak_db:.6f}" if peak_db is not None else "",
        "track_perceived_db": f"{perceived_db:.6f}" if perceived_db is not None else "",
    }


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
