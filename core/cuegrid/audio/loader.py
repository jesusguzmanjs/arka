"""Audio utilities used by the active application.

The detector owns its one full-track decode and RAM slicing. This module is
limited to the independent GUI preview payload and a general seek-window
utility. Legacy Stem and FFmpeg helpers live in :mod:`cuegrid.audio.legacy_stems`
and are deliberately not imported here.
"""

from __future__ import annotations

import librosa
import numpy as np
from pathlib import Path

PREVIEW_SAMPLE_RATE = 11_025
PREVIEW_PEAK_WINDOW = 64
PREVIEW_HOP_LENGTH = 512
PREVIEW_BUCKET_MS = 500


# def generate_preview_payload(
#     path: str | Path,
# ) -> tuple[list[int], list[dict[str, float]]]:
#     """Generate the renderer-ready waveform and three-band colour map.
#
#     Preview generation is intentionally independent of detection and uses one
#     low-rate full decode. It is used only by ``--get-track-metadata``.
#     """
#     import warnings
#
#     with warnings.catch_warnings():
#         warnings.filterwarnings("ignore", category=UserWarning, module="soundfile")
#         y, _ = librosa.load(str(path), sr=PREVIEW_SAMPLE_RATE, mono=True)
#
#     if y.size == 0:
#         return [], []
#
#     full_length = (y.size // PREVIEW_PEAK_WINDOW) * PREVIEW_PEAK_WINDOW
#     peak_chunks = y[:full_length].reshape(-1, PREVIEW_PEAK_WINDOW)
#     if peak_chunks.size:
#         wave_min = peak_chunks.min(axis=1)
#         wave_max = peak_chunks.max(axis=1)
#         peaks = np.empty(wave_min.size + wave_max.size, dtype=np.float32)
#         peaks[0::2] = wave_min
#         peaks[1::2] = wave_max
#         peaks = np.sign(peaks) * (np.abs(peaks) ** 1.8)
#         waveform_peaks = (peaks * 127).astype(np.int8).tolist()
#     else:
#         waveform_peaks = []
#
#     spectrum = np.abs(librosa.stft(y, n_fft=512, hop_length=PREVIEW_HOP_LENGTH))
#     frequencies = librosa.fft_frequencies(sr=PREVIEW_SAMPLE_RATE, n_fft=512)
#     low_end = np.where(frequencies < 250)[0][-1]
#     mid_end = np.where(frequencies < 2500)[0][-1]
#     low_energy = spectrum[:low_end, :].sum(axis=0)
#     mid_energy = spectrum[low_end:mid_end, :].sum(axis=0)
#     high_energy = spectrum[mid_end:, :].sum(axis=0)
#
#     frames_per_bucket = max(
#         1,
#         round(
#             PREVIEW_BUCKET_MS / 1000 * PREVIEW_SAMPLE_RATE / PREVIEW_HOP_LENGTH
#         ),
#     )
#     trim_frames = len(low_energy) - (len(low_energy) % frames_per_bucket)
#     if trim_frames == 0:
#         return waveform_peaks, []
#
#     low_buckets = low_energy[:trim_frames].reshape(-1, frames_per_bucket).mean(axis=1)
#     mid_buckets = mid_energy[:trim_frames].reshape(-1, frames_per_bucket).mean(axis=1)
#     high_buckets = high_energy[:trim_frames].reshape(-1, frames_per_bucket).mean(axis=1)
#     maximum = max(low_buckets.max(), mid_buckets.max(), high_buckets.max())
#     if maximum > 0:
#         low_buckets = low_buckets / maximum
#         mid_buckets = mid_buckets / maximum
#         high_buckets = high_buckets / maximum
#
#     color_map = [
#         {"l": round(float(low), 4), "m": round(float(mid), 4), "h": round(float(high), 4)}
#         for low, mid, high in zip(low_buckets, mid_buckets, high_buckets)
#     ]
#     return waveform_peaks, color_map
#

def generate_preview_payload(
    path: str | Path,
) -> tuple[list[int], list[dict[str, float]]]:
    """Generate the renderer-ready waveform peaks.

    V1.0 OPTIMIZATION: Spectrum (color_map) generation via STFT has been
    disabled to drastically reduce loading times. Returns an empty color map
    to maintain compatibility with the frontend signature.
    """
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, module="soundfile")
        # El downsampling a 11kHz ya es rapidísimo
        y, _ = librosa.load(str(path), sr=PREVIEW_SAMPLE_RATE, mono=True)

    if y.size == 0:
        return [], []

    # 1. Cálculo rápido de picos (Amplitud pura)
    full_length = (y.size // PREVIEW_PEAK_WINDOW) * PREVIEW_PEAK_WINDOW
    peak_chunks = y[:full_length].reshape(-1, PREVIEW_PEAK_WINDOW)
    if peak_chunks.size:
        wave_min = peak_chunks.min(axis=1)
        wave_max = peak_chunks.max(axis=1)
        peaks = np.empty(wave_min.size + wave_max.size, dtype=np.float32)
        peaks[0::2] = wave_min
        peaks[1::2] = wave_max

        # --- AJUSTES VISUALES V1.0 ---
        EXPONENT = 1.35  # Curva más suave: conserva detalle en temas con mucho volumen/limitador
        HEADROOM = 0.78  # Escala la onda al 78% del alto para evitar el efecto 'bloque' en los bordes

        peaks = np.sign(peaks) * (np.abs(peaks) ** EXPONENT)
        waveform_peaks = (peaks * (127 * HEADROOM)).astype(np.int8).tolist()
    else:
        waveform_peaks = []

    # 2. Espectro Desactivado (Se ahorran ~2-3 segundos por track)
    # El frontend debe estar preparado para recibir una lista vacía
    # y simplemente pintar la onda del color corporativo por defecto.
    color_map = []

    return waveform_peaks, color_map

def load_window(
    path: str | Path,
    offset_sec: float,
    duration_sec: float,
    sr: int | None = None,
) -> tuple[np.ndarray, int]:
    """Decode one requested mono window with ``librosa.load``.

    This compatibility utility is not used by the active detector, which
    slices its single full-track decode in memory.
    """
    if offset_sec < 0:
        raise ValueError(f"offset_sec must be >= 0, got {offset_sec!r}")
    if duration_sec <= 0:
        raise ValueError(f"duration_sec must be > 0, got {duration_sec!r}")

    y, actual_sr = librosa.load(
        str(path), sr=sr, mono=True, offset=offset_sec, duration=duration_sec
    )
    return y, int(actual_sr)
