"""Quick verification script for Grid-Guided Phrase Candidate Generation.

Ties together two already-implemented, independently-tested pieces
(per ``.openspec/2-spec.md`` sections 2.1, 4, and 7):

1. ``traktorco.nml.parser.NmlParser`` -- extracts BPM, grid anchor, and
   duration for a track from a ``collection.nml``.
2. ``traktorco.audio.beatgrid.generate_phrase_candidates`` -- pure math
   that turns those three numbers into a list of grid-locked
   ``PhraseCandidate``s (every 16/32 beats).

This script does not belong in ``audio/beatgrid.py`` itself: per spec
section 2.1, ``audio.beatgrid`` must never read files. Composing the two
modules is the future job of ``core.pipeline`` (not yet implemented) --
this script is a standalone, throwaway way to eyeball the math against a
real NML fixture before that orchestration layer exists.

Usage:
    python scripts/verify_phrase_candidates.py [nml_path] [track_path]

With no arguments, defaults to ``tests/fixtures/sample_collection.nml``
and its one known track.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from traktorco.audio.beatgrid import generate_phrase_candidates  # noqa: E402
from traktorco.nml.parser import NmlParser  # noqa: E402

DEFAULT_NML = _PROJECT_ROOT / "tests" / "fixtures" / "sample_collection.nml"
DEFAULT_TRACK_PATH = r"C:\Users\ska_m\Music\Tidal\Machinedrum - NO 1 KNEW.flac"


def format_timestamp(time_ms: float) -> str:
    total_seconds = time_ms / 1000.0
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:06.3f}"


def main() -> None:
    nml_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NML
    track_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TRACK_PATH

    parser = NmlParser(nml_path)
    entry = parser.find_entry(track_path)

    print(f"Track:       {entry.artist} - {entry.title}")
    print(f"BPM:         {entry.tempo.bpm:.6f}")
    print(f"Grid anchor: {entry.grid_anchor_ms:.3f} ms")
    print(
        f"Duration:    {entry.duration_ms:.3f} ms "
        f"({format_timestamp(entry.duration_ms)})"
    )
    print()

    candidates = generate_phrase_candidates(
        bpm=entry.tempo.bpm,
        grid_anchor_ms=entry.grid_anchor_ms,
        duration_ms=entry.duration_ms,
    )

    print(
        f"Generated {len(candidates)} phrase candidates "
        f"(phrase_beats=16, major_phrase_multiple=2):\n"
    )
    header = f"{'n':>4}  {'beat_index':>10}  {'time_ms':>12}  {'m:ss.mmm':>10}  major?"
    print(header)
    print("-" * len(header))
    for n, candidate in enumerate(candidates):
        marker = "32-beat" if candidate.is_major_phrase else ""
        print(
            f"{n:>4}  {candidate.beat_index:>10}  {candidate.time_ms:>12.3f}  "
            f"{format_timestamp(candidate.time_ms):>10}  {marker}"
        )


if __name__ == "__main__":
    main()
