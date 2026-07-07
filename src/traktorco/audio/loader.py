"""Targeted, seek-based audio window decoding.

Implements the ``audio.loader`` responsibility from ``.openspec/2-spec.md``
section 2.1: decode only a small requested window of audio (via
``offset``/``duration``), never a whole track. Track duration comes from
the NML's ``<INFO>`` element (see section 2.3), not from this module, so
there is intentionally no whole-file ``load_audio()`` function here.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np


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
        path: Path to the audio file.
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
    return y, actual_sr
