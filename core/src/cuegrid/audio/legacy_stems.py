"""Dormant native-Stem helpers retained outside the production import graph.

Nothing in the active CLI, pipeline, detector, or loader imports this module.
It is also explicitly excluded from the PyInstaller build.  The optional
``ffmpeg-python`` package is resolved only when ``extract_drum_stem`` is called
in a separate legacy environment, so retaining this reference code does not
add an FFmpeg dependency to CueGrid.
"""

from __future__ import annotations

import importlib
import logging
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

DRUMS_STEM_STREAM_INDEX = 1
_ENERGY_PROBE_CHUNK_COUNT = 8
_ENERGY_PROBE_CHUNK_SEC = 0.5
DRUM_STEM_SILENCE_RMS_THRESHOLD = 0.01


def _load_ffmpeg() -> Any:
    """Load the optional legacy dependency only when legacy extraction runs."""
    try:
        return importlib.import_module("ffmpeg")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Legacy Stem extraction requires the optional 'ffmpeg-python' package; "
            "it is intentionally not installed with CueGrid."
        ) from exc


def extract_drum_stem(
    stem_path: str | Path,
    stream_index: int = DRUMS_STEM_STREAM_INDEX,
) -> Path:
    """Legacy-only: demux one stream from a native ``.stem.mp4`` sidecar.

    This function is retained for reference and requires a separately installed
    ``ffmpeg-python`` package plus an FFmpeg executable. It is not supported by
    the packaged CueGrid application.
    """
    ffmpeg = _load_ffmpeg()
    tmp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = Path(tmp_file.name)
    tmp_file.close()

    try:
        (
            ffmpeg.input(str(stem_path))
            .output(
                str(tmp_path),
                map=f"0:{stream_index}",
                acodec="pcm_s16le",
                loglevel="error",
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        stderr_bytes = getattr(exc, "stderr", None)
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else str(exc)
        logger.warning(
            "Legacy ffmpeg extraction failed for stream 0:%d from %s: %s",
            stream_index,
            stem_path,
            stderr,
        )
        raise

    return tmp_path


def measure_audio_energy(
    path: str | Path,
    chunk_count: int = _ENERGY_PROBE_CHUNK_COUNT,
    chunk_sec: float = _ENERGY_PROBE_CHUNK_SEC,
) -> float:
    """Legacy-only: estimate average RMS energy from short seek-based reads."""
    with sf.SoundFile(str(path)) as audio_file:
        total_frames = len(audio_file)
        sample_rate = audio_file.samplerate
        if total_frames == 0 or sample_rate == 0:
            return 0.0

        chunk_frames = max(1, int(chunk_sec * sample_rate))
        usable_span = max(total_frames - chunk_frames, 0)
        starts = [
            int(usable_span * index / max(chunk_count - 1, 1))
            if chunk_count > 1
            else 0
            for index in range(chunk_count)
        ]
        rms_samples: list[float] = []
        for start in starts:
            audio_file.seek(start)
            data = audio_file.read(
                frames=chunk_frames, dtype="float32", always_2d=True
            )
            if data.size:
                mono = data.mean(axis=1)
                rms_samples.append(float(np.sqrt(np.mean(np.square(mono)))))

    return float(np.mean(rms_samples)) if rms_samples else 0.0


def is_drum_stem_empty(
    path: str | Path, threshold: float = DRUM_STEM_SILENCE_RMS_THRESHOLD
) -> bool:
    """Legacy-only: identify a practically silent extracted drum stem."""
    return measure_audio_energy(path) < threshold
