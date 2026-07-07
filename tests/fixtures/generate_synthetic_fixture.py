"""Synthetic audio fixture generator for `tests/audio/test_detector.py`.

Generates a short WAV file with a known, fixed BPM/grid and a deliberate,
massive energy + timbre jump exactly at a known 16-beat phrase boundary,
so `audio.detector.detect_events` (spec section 6) can be tested
deterministically without needing a real music file.

Design (deliberately deterministic, not noise-based): the track is a
quiet, pure sine tone from 0s up to the jump, then an abrupt switch to a
loud, harmonically-rich square wave for the remainder of the track. Both
halves are *periodic* signals, so any two same-length windows taken from
entirely within one half or the other have (up to negligible frame-edge
effects) identical RMS energy and MFCC timbre -- there is no
random-noise-vs-random-noise variance to make the test flaky. Only a
window straddling the jump boundary sees a real change.

Run directly to (re)generate the checked-in fixture:

    python tests/fixtures/generate_synthetic_fixture.py

The constants below are the single source of truth for both the
generated audio and `tests/audio/test_detector.py`'s expectations --
import them rather than hard-coding duplicate values.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

# --- Fixture parameters (shared with tests/audio/test_detector.py) --------

SAMPLE_RATE = 22_050
BPM = 120.0
GRID_ANCHOR_MS = 0.0
PHRASE_BEATS = 16

# 3 phrase candidates past the anchor at 120 BPM (500ms/beat, 16 beats =
# 8000ms/candidate): 0ms, 8000ms, 16000ms, 24000ms. DURATION_MS is chosen
# so exactly these 4 candidates are generated (the next, at 32000ms,
# would exceed the track's duration).
DURATION_MS = 26_000.0

# The jump lands exactly on the 3rd phrase candidate (beat_index=32, a
# "major" 32-beat boundary per major_phrase_multiple=2).
JUMP_BEAT_INDEX = 32
_BEAT_LENGTH_MS = 60_000.0 / BPM
JUMP_TIME_MS = GRID_ANCHOR_MS + JUMP_BEAT_INDEX * _BEAT_LENGTH_MS

BASELINE_FREQ_HZ = 220.0  # quiet A3 sine tone before the jump
BASELINE_AMPLITUDE = 0.02
JUMP_FREQ_HZ = 880.0  # loud, harmonically-rich square wave after the jump
JUMP_AMPLITUDE = 0.8

FIXTURE_PATH = Path(__file__).parent / "sample_track.wav"


def generate_samples() -> np.ndarray:
    """Build the full waveform: quiet baseline tone, then a massive jump."""
    duration_sec = DURATION_MS / 1000.0
    jump_time_sec = JUMP_TIME_MS / 1000.0
    n_samples = int(round(duration_sec * SAMPLE_RATE))
    jump_sample = int(round(jump_time_sec * SAMPLE_RATE))

    t = np.arange(n_samples) / SAMPLE_RATE

    baseline = BASELINE_AMPLITUDE * np.sin(2.0 * np.pi * BASELINE_FREQ_HZ * t)
    # A square wave via sign(sin(...)) is periodic and harmonically rich
    # (many odd harmonics), giving both a large energy jump and a large
    # timbral (MFCC) jump relative to the pure baseline sine.
    jump = JUMP_AMPLITUDE * np.sign(np.sin(2.0 * np.pi * JUMP_FREQ_HZ * t))

    y = baseline.astype(np.float32)
    y[jump_sample:] = jump[jump_sample:].astype(np.float32)
    return y


def generate(path: Path = FIXTURE_PATH) -> Path:
    """Generate and write the synthetic fixture to ``path``. Returns ``path``."""
    y = generate_samples()
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), y, SAMPLE_RATE, subtype="PCM_16")
    return path


def main() -> None:
    written = generate()
    print(f"Wrote synthetic fixture: {written}")
    print(f"  duration={DURATION_MS / 1000:.1f}s  sample_rate={SAMPLE_RATE}Hz")
    print(f"  bpm={BPM}  grid_anchor_ms={GRID_ANCHOR_MS}")
    print(f"  jump_beat_index={JUMP_BEAT_INDEX}  jump_time_ms={JUMP_TIME_MS}")


if __name__ == "__main__":
    main()
