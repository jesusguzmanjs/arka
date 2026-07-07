"""Quick verification script for Grid-Guided Phrase Analysis (spec section 6).

Loads a track's BPM/grid anchor/duration from a Traktor collection.nml (or
accepts manual overrides), runs `traktorco.audio.detector.detect_events`
against the *real* audio file, and prints the confirmed DetectedEvents
(label, timestamp, and confidence score) to the terminal.

Usage:
    python scripts/verify_detected_events.py AUDIO_PATH [--nml NML_PATH]
        [--bpm BPM] [--grid-anchor-ms MS] [--duration-ms MS]

By default, BPM/grid-anchor/duration are read from the <ENTRY> in
NML_PATH (default: tests/fixtures/sample_collection.nml) whose <LOCATION>
matches AUDIO_PATH exactly (see .openspec/2-spec.md section 7) -- so the
simplest way to test your own track is to either catalog it in Traktor
first, or point --nml at a collection.nml that already has it.

If you just want to smoke-test the detector against an arbitrary audio
file with no matching NML entry, supply --bpm, --grid-anchor-ms, and
--duration-ms explicitly to skip the NML lookup entirely.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from traktorco.audio.detector import detect_events  # noqa: E402
from traktorco.config import AppConfig  # noqa: E402
from traktorco.nml.parser import NmlParser, TrackNotFoundError  # noqa: E402

DEFAULT_NML = _PROJECT_ROOT / "tests" / "fixtures" / "sample_collection.nml"


def format_timestamp(time_ms: float) -> str:
    total_seconds = time_ms / 1000.0
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:06.3f}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "audio_path", type=Path, help="Path to the audio file to analyze"
    )
    parser.add_argument(
        "--nml",
        type=Path,
        default=DEFAULT_NML,
        help="Path to a collection.nml (default: test fixture)",
    )
    parser.add_argument("--bpm", type=float, default=None, help="Manual BPM override")
    parser.add_argument(
        "--grid-anchor-ms",
        type=float,
        default=None,
        help="Manual grid anchor override, in ms",
    )
    parser.add_argument(
        "--duration-ms",
        type=float,
        default=None,
        help="Manual duration override, in ms",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manual_override = (
        args.bpm is not None
        and args.grid_anchor_ms is not None
        and args.duration_ms is not None
    )

    if manual_override:
        bpm, grid_anchor_ms, duration_ms = (
            args.bpm,
            args.grid_anchor_ms,
            args.duration_ms,
        )
        print("Using manual BPM/grid-anchor/duration overrides (NML lookup skipped).")
    else:
        try:
            entry = NmlParser(args.nml).find_entry(args.audio_path)
        except TrackNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(
                "\nThe audio file's path must exactly match an <ENTRY>'s <LOCATION> "
                "in the collection.nml (spec section 7). Either catalog this track "
                "in Traktor first, point --nml at a collection that already has it, "
                "or supply --bpm/--grid-anchor-ms/--duration-ms to bypass the NML "
                "lookup entirely.",
                file=sys.stderr,
            )
            sys.exit(1)
        bpm = args.bpm if args.bpm is not None else entry.tempo.bpm
        grid_anchor_ms = (
            args.grid_anchor_ms
            if args.grid_anchor_ms is not None
            else entry.grid_anchor_ms
        )
        duration_ms = (
            args.duration_ms if args.duration_ms is not None else entry.duration_ms
        )
        print(f"Track:       {entry.artist} - {entry.title}")

    print(f"BPM:         {bpm:.6f}")
    print(f"Grid anchor: {grid_anchor_ms:.3f} ms")
    print(f"Duration:    {duration_ms:.3f} ms ({format_timestamp(duration_ms)})")
    print()

    config = AppConfig()
    print(
        f"Config: phrase_beats={config.phrase_beats}, window_beats={config.window_beats}, "
        f"energy_change_threshold_db={config.energy_change_threshold_db}, "
        f"timbre_change_distance_threshold={config.timbre_change_distance_threshold}\n"
    )

    events = detect_events(
        audio_path=args.audio_path,
        bpm=bpm,
        grid_anchor_ms=grid_anchor_ms,
        duration_ms=duration_ms,
        config=config,
    )

    if not events:
        print(
            "No confirmed DetectedEvents (no significant energy/timbre change "
            "found at any phrase boundary)."
        )
        return

    print(f"Confirmed {len(events)} DetectedEvent(s):\n")
    header = f"{'label':>12}  {'time_ms':>12}  {'m:ss.mmm':>10}  {'beat_index':>10}  major?  confidence"
    print(header)
    print("-" * len(header))
    for event in events:
        marker = "32-beat" if event.is_major_phrase else ""
        print(
            f"{event.label:>12}  {event.time_ms:>12.3f}  "
            f"{format_timestamp(event.time_ms):>10}  {event.beat_index:>10}  "
            f"{marker:>7}  {event.confidence:.3f}"
        )


if __name__ == "__main__":
    main()
