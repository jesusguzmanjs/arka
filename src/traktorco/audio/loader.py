"""Targeted, seek-based audio window decoding.

Implements the ``audio.loader`` responsibility from ``.openspec/2-spec.md``
section 2.1: decode only a small requested window of audio (via
``offset``/``duration``), never a whole track. Track duration comes from
the NML's ``<INFO>`` element (see section 2.3), not from this module, so
there is intentionally no whole-file ``load_audio()`` function here.

v2.0 Stems Integration (spec section 9) additionally allows the pipeline
to analyze a single isolated stem *stream* inside a native Traktor
``.stem.mp4`` sidecar instead of the original mixed-down audio file.
``extract_drum_stem`` demuxes that one stream (via ``ffmpeg``) to a
temporary mono WAV file once per track; the resulting path is then handed
to the exact same ``load_window`` seek path used for ordinary files, so
``audio.detector``/``audio.features`` are completely unaware of where the
samples came from -- no core detection math changes at all.

v2.2 Multi-Source Validation (spec section 10) adds ``measure_audio_energy``
and ``is_drum_stem_empty``: a handful of short, evenly-spaced seek-based
reads (via ``soundfile``) used to cheaply estimate a whole file's overall
RMS energy without ever decoding it in full -- specifically so
``core.pipeline`` can detect a practically-silent/ambient drum stem (e.g.
Ambient/IDM tracks with no real drum content) and fall back to the
original Master audio instead of analyzing silence.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import ffmpeg
import librosa
import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# NI's native stem format multiplexes 5 audio streams into one .stem.mp4:
# stream 0 is the original full mix, and streams 1-4 are the four isolated
# stems (Drums, Bass, Vocals, Melody/Other, per NI's own stem template).
# Stream index 1 ("0:1" in ffmpeg's stream-specifier syntax) is the
# Drums/Rhythm stem -- what this project wants to feed the phrase
# detector instead of the full mix.
DRUMS_STEM_STREAM_INDEX = 1


def load_window(
    path: str | Path,
    offset_sec: float,
    duration_sec: float,
    sr: int | None = None,
) -> tuple[np.ndarray, int]:
    """Decode a short window of audio via ``librosa.load(offset=, duration=)``.

    For seekable formats, ``librosa`` (via ``soundfile``) seeks directly to
    ``offset_sec`` rather than decoding the whole file up to that point --
    this is what keeps Grid-Guided Phrase Analysis (spec section 6) cheap
    even on long tracks.

    Args:
        path: Path to the audio file. May be an original track file, or a
            temporary drum-stem WAV produced by ``extract_drum_stem``
            (spec section 9) -- this function treats both identically.
        offset_sec: Start of the window, in seconds. Must be ``>= 0``.
            Callers (``audio.detector``) are responsible for skipping
            windows that would start before the track begins rather than
            clamping the offset to ``0`` (spec section 6.1, step 3) --
            this function deliberately does not clamp, so a negative
            offset is a caller bug, not a valid edge case.
        duration_sec: Length of the window, in seconds. Must be ``> 0``.
            If the window would run past the end of the file, ``librosa``
            simply returns fewer samples; callers should pre-truncate
            ``duration_sec`` to what remains in the track when they know
            that bound (spec section 6.1, step 3), but this function does
            not require it.
        sr: Target sample rate, or ``None`` to keep the file's native rate.

    Returns:
        ``(y, sr)`` -- the decoded mono waveform and its sample rate.

    Raises:
        ValueError: if ``offset_sec < 0`` or ``duration_sec <= 0``.
    """
    if offset_sec < 0:
        raise ValueError(
            f"offset_sec must be >= 0, got {offset_sec!r}; callers must skip "
            "windows that would start before the track begins rather than "
            "clamping the offset (spec section 6.1, step 3)"
        )
    if duration_sec <= 0:
        raise ValueError(f"duration_sec must be > 0, got {duration_sec!r}")

    y, actual_sr = librosa.load(
        str(path), sr=sr, mono=True, offset=offset_sec, duration=duration_sec
    )
    return y, int(actual_sr)


def extract_drum_stem(
    stem_path: str | Path,
    stream_index: int = DRUMS_STEM_STREAM_INDEX,
) -> Path:
    """Demux one isolated stem stream out of a native ``.stem.mp4`` sidecar.

    Implements the "Audio Extraction" step of the v2.0 Stems Integration
    architecture (spec section 9.2): runs ``ffmpeg`` (via the
    ``ffmpeg-python`` bindings) to extract stream ``0:<stream_index>``
    (the Drums/Rhythm stem by default) into a temporary mono PCM WAV
    file. The caller owns the returned path and is responsible for
    deleting it once analysis of the track is complete (``core.pipeline``
    does this in a ``finally`` block).

    Args:
        stem_path: Path to the native ``.stem.mp4`` sidecar on disk.
        stream_index: Which audio stream to extract, as the ``N`` in
            ffmpeg's ``0:N`` stream specifier. Defaults to
            ``DRUMS_STEM_STREAM_INDEX`` (the Drums/Rhythm stem).

    Returns:
        Path to a newly created temporary ``.wav`` file containing only
        the requested stem's audio.

    Raises:
        ffmpeg.Error: if ``ffmpeg`` fails to decode/demux the requested
            stream (e.g. corrupt sidecar, unexpected stream layout).
    """
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
        # Extraemos el stderr de forma segura por si es un error de Subprocess
        stderr_bytes = getattr(exc, "stderr", None)
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else str(exc)

        logger.warning(
            "ffmpeg failed to extract stream 0:%d from %s: %s",
            stream_index,
            stem_path,
            stderr,
        )
        raise

    logger.debug(
        "Extracted stream 0:%d from %s to temporary file %s",
        stream_index,
        stem_path,
        tmp_path,
    )
    return tmp_path


# v2.2 Multi-Source Validation (spec section 10.1): how many short chunks to
# sample when estimating a file's overall energy, and how long each chunk
# is. Kept small and fixed so this check stays "lightning-fast" even on a
# long track -- it is a handful of tiny seek-based reads, never a full
# decode.
_ENERGY_PROBE_CHUNK_COUNT = 8
_ENERGY_PROBE_CHUNK_SEC = 0.5


def measure_audio_energy(
    path: str | Path,
    chunk_count: int = _ENERGY_PROBE_CHUNK_COUNT,
    chunk_sec: float = _ENERGY_PROBE_CHUNK_SEC,
) -> float:
    """Cheaply estimate a whole file's overall RMS energy.

    Implements the "Empty Stem Detection" probe of v2.2 Multi-Source
    Validation (spec section 10.1): reads ``chunk_count`` short,
    evenly-spaced chunks (``chunk_sec`` seconds each) via ``soundfile``,
    seeking directly to each chunk rather than decoding anything in
    between -- this is what keeps the check "lightning-fast" even on a
    long track.

    Args:
        path: Path to the audio file (or temporary drum-stem WAV) to probe.
        chunk_count: How many evenly-spaced chunks to sample.
        chunk_sec: Length of each sampled chunk, in seconds.

    Returns:
        The mean RMS energy across all sampled chunks. ``0.0`` if the
        file has no frames at all (rather than raising).
    """
    with sf.SoundFile(str(path)) as f:
        total_frames = len(f)
        sr = f.samplerate
        if total_frames == 0 or sr == 0:
            return 0.0

        chunk_frames = max(1, int(chunk_sec * sr))
        # Evenly-spaced start offsets across the file, each far enough from
        # the end to read a full chunk where possible.
        usable_span = max(total_frames - chunk_frames, 0)
        starts = [
            int(usable_span * i / max(chunk_count - 1, 1)) if chunk_count > 1 else 0
            for i in range(chunk_count)
        ]

        rms_samples: list[float] = []
        for start in starts:
            f.seek(start)
            data = f.read(frames=chunk_frames, dtype="float32", always_2d=True)
            if data.size == 0:
                continue
            # Collapse to mono if multi-channel, matching load_window's
            # mono=True behavior, before computing RMS.
            mono = data.mean(axis=1)
            rms_samples.append(float(np.sqrt(np.mean(np.square(mono)))))

    return float(np.mean(rms_samples)) if rms_samples else 0.0


# v2.2 (spec section 10.1): below this mean RMS, a drum stem is considered
# "empty" (practically silent/ambient) and analysis should fall back to the
# original Master audio rather than risk analyzing silence.
DRUM_STEM_SILENCE_RMS_THRESHOLD = 0.01


def is_drum_stem_empty(
    path: str | Path, threshold: float = DRUM_STEM_SILENCE_RMS_THRESHOLD
) -> bool:
    """Return ``True`` if a drum stem's overall energy is below the silence threshold.

    Implements the v2.2 "Empty Stem Detection" guard (spec section 10.1):
    used by ``core.pipeline`` right after ``extract_drum_stem`` to protect
    fast mode from analyzing a practically drumless stem (e.g. Ambient or
    IDM tracks), falling back to the original Master audio instead.
    """
    energy = measure_audio_energy(path)
    return energy < threshold
