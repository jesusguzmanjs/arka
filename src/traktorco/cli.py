"""Command-line entry point.

Implements the ``cli`` responsibility from ``.openspec/2-spec.md``
section 2.1: argument parsing and logging setup only, delegating all
business logic to ``core.pipeline``. Also implements the
``--title``/``--artist`` disambiguation flags required by spec section
7.3, step 6, and OS-aware auto-discovery of the default
``collection.nml`` when ``--nml`` is not supplied.
"""

from __future__ import annotations

import argparse
import logging
import platform
import sys
from pathlib import Path
from typing import Any, cast

from traktorco.config import DETECTION_MODES, AppConfig
from traktorco.core.pipeline import run_batch_pipeline, run_pipeline
from traktorco.nml.parser import (
    AmbiguousPlaylistError,
    AmbiguousTrackError,
    PlaylistNotFoundError,
    TrackNotFoundError,
)

logger = logging.getLogger(__name__)

# Matches Native Instruments' own documented default install layout on
# both Windows and macOS: "Documents > Native Instruments > Traktor x.x.x"
# (see .openspec/2-spec.md section 7.1's VOLUME/path notes). Traktor
# creates a new "Traktor x.x.x" folder per version under this root, so we
# glob for all of them and pick the most recently modified collection.nml.
_TRAKTOR_VERSION_DIR_GLOB = "Traktor *"


def _traktor_root_directories() -> list[Path]:
    """Return the standard Native Instruments root directories to search.

    OS-aware: Windows and macOS both default to
    ``~/Documents/Native Instruments``; other platforms are not an
    official Traktor target, but a couple of plausible fallback locations
    are included so discovery degrades gracefully rather than assuming
    Windows/macOS unconditionally.
    """
    home = Path.home()
    system = platform.system()

    if system in ("Windows", "Darwin"):
        return [home / "Documents" / "Native Instruments"]

    # Unofficial/unsupported platform: best-effort fallbacks only.
    return [
        home / "Documents" / "Native Instruments",
        home / ".native-instruments",
    ]


def discover_collection_nml_paths() -> list[Path]:
    """Find every ``collection.nml`` under the standard Traktor root directories.

    Searches ``<root>/Traktor */collection.nml`` (one glob match per
    installed Traktor version) across every root directory returned by
    ``_traktor_root_directories()``.
    """
    found: list[Path] = []
    for root in _traktor_root_directories():
        if not root.is_dir():
            continue
        for version_dir in sorted(root.glob(_TRAKTOR_VERSION_DIR_GLOB)):
            candidate = version_dir / "collection.nml"
            if candidate.is_file():
                found.append(candidate)
    return found


def discover_default_nml_path() -> Path | None:
    """Pick the most recently modified ``collection.nml`` among discovered candidates.

    Used as the ``--nml`` default when the flag is not supplied. Returns
    ``None`` if no candidates were found under any standard Traktor root
    directory -- callers must treat that as "the user must pass --nml
    explicitly", not crash.
    """
    candidates = discover_collection_nml_paths()
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cuegrid",
        description=(
            "Analyze track(s) with Grid-Guided Phrase Analysis and inject "
            "confirmed HotCues into a Traktor collection.nml."
        ),
    )

    # Mutually exclusive track selection group (spec section 8.4)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "track_path",
        nargs="?",
        type=Path,
        default=None,
        help="Path to the audio file to analyze (v1.0 single-track mode)",
    )
    selection.add_argument(
        "--track-title",
        type=str,
        default=None,
        help="Batch mode: process all tracks matching this TITLE",
    )
    selection.add_argument(
        "--playlist",
        type=str,
        default=None,
        help="Batch mode: process all tracks in the named playlist",
    )

    parser.add_argument(
        "--nml",
        type=Path,
        default=None,
        help=(
            "Path to the Traktor collection.nml to read from and write back "
            "to. If omitted, the most recently modified collection.nml under "
            "the standard Traktor install directories is used."
        ),
    )
    parser.add_argument(
        "--stems-dir",
        type=Path,
        default=None,
        dest="stems_dir",
        help=(
            "Path to Traktor's native Stems root directory, for v2.0 Stems "
            "Integration path prediction (spec section 9). If omitted, "
            "auto-discovered: first ~/Music/Traktor/Stems/ (Traktor's own "
            "default), falling back to a Stems/ folder next to --nml if "
            "that does not exist. Only needed if you have repointed "
            "Traktor's stem storage location in its preferences."
        ),
    )
    parser.add_argument(
        "--artist",
        type=str,
        default=None,
        help=(
            "Disambiguate by track ARTIST. In single-track mode, used with "
            "--title to resolve ambiguous paths. In batch mode (--track-title), "
            "narrows the title search."
        ),
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help=(
            "(Single-track mode only) Disambiguate by track TITLE if the path "
            "alone matches multiple ENTRYs. Not allowed with --track-title or --playlist."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable INFO-level logging"
    )
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        default=False,
        help=(
            "Remove all existing standard HotCues from the matched ENTRY before "
            "writing new ones. Grid markers (AutoGrid) and Load markers are never "
            "removed. Use this when you want a clean slate of auto-generated cues."
        ),
    )
    parser.add_argument(
        "--verify",
        type=str,
        choices=["fast", "smart"],
        default="fast",
        help=(
            "Multi-Source Validation mode (v2.2, spec section 10). 'fast' "
            "(default) analyzes only the isolated drum stem (with an "
            "automatic fallback to the Master track if the stem is "
            "empty/ambient). 'smart' additionally cross-checks each "
            "candidate against the Master audio, labeling it 'Drop (Rhythm)' "
            "or 'Breakdown (Melodic)'."
        ),
    )

    # Validation: --title is only valid with track_path (single-track mode)
    # This is checked in main() rather than here since argparse group rules are limited

    tuning = parser.add_argument_group(
        "Grid-Guided Phrase Analysis tuning (advanced)",
        "Any flag omitted here falls back to the AppConfig dataclass default "
        "(spec section 2.2) -- these are not required for normal use.",
    )
    tuning.add_argument(
        "--mode",
        type=str,
        choices=["soft", "medium", "hard"],
        default=None,
        help=(
            "Dynamic sensitivity preset: 'soft' (energy=2.0, timbre=8.0), "
            "'medium' (energy=4.0, timbre=18.0, default), "
            "'hard' (energy=7.0, timbre=30.0). "
            "Overrides --energy-threshold and --timbre-threshold when set."
        ),
    )
    tuning.add_argument(
        "--phrase-beats",
        type=int,
        default=None,
        help=f"Base phrase granularity, in beats (default: {AppConfig.phrase_beats})",
    )
    tuning.add_argument(
        "--major-phrase-multiple",
        type=int,
        default=None,
        help=(
            "Every Nth candidate is also tagged a 'major' phrase boundary "
            f"(default: {AppConfig.major_phrase_multiple})"
        ),
    )
    tuning.add_argument(
        "--sample-rate",
        type=int,
        default=None,
        help="Resample windows to this rate; omit to keep each track's native rate",
    )
    tuning.add_argument(
        "--hop-length",
        type=int,
        default=None,
        help=f"Frame hop for RMS/MFCC extraction (default: {AppConfig.hop_length})",
    )
    tuning.add_argument(
        "--window-beats",
        type=float,
        default=None,
        help=(
            "Before/after analysis window size, in beats "
            f"(default: {AppConfig.window_beats})"
        ),
    )
    tuning.add_argument(
        "--mfcc-count",
        type=int,
        default=None,
        help=f"Number of MFCC coefficients to extract (default: {AppConfig.mfcc_count})",
    )
    tuning.add_argument(
        "--energy-threshold",
        type=float,
        default=None,
        help=(
            "Minimum |energy delta| in dB to flag a significant change "
            f"(default: {AppConfig.energy_change_threshold_db})"
        ),
    )
    tuning.add_argument(
        "--timbre-threshold",
        type=float,
        default=None,
        help=(
            "Minimum Euclidean MFCC distance to flag a significant change "
            f"(default: {AppConfig.timbre_change_distance_threshold})"
        ),
    )
    tuning.add_argument(
        "--max-cues",
        type=int,
        default=None,
        help=f"Cap on how many cues are written per track (default: {AppConfig.max_cues})",
    )
    tuning.add_argument(
        "--relative-confidence-threshold",
        type=float,
        default=None,
        help=(
            "Keep only candidates whose confidence is at least this fraction "
            "of the track's strongest candidate "
            f"(default: {AppConfig.relative_confidence_threshold})"
        ),
    )

    export = parser.add_argument_group(
        "Data export (v1.8)",
        "Export per-candidate telemetry to CSV for offline analysis and tuning.",
    )
    export.add_argument(
        "--export-csv",
        type=Path,
        default=None,
        dest="export_csv_path",
        metavar="PATH",
        help="Write per-candidate metrics to a CSV file for data-driven tuning",
    )
    return parser


# Maps each CLI flag's argparse dest to its AppConfig field name. Kept as a
# single source of truth so build_config_from_args() never has to duplicate
# AppConfig's own default values -- an omitted flag simply never appears in
# the overrides dict, and AppConfig(**overrides) falls back to its own
# dataclass default for that field automatically.
_CONFIG_FIELD_BY_ARG_DEST = {
    "phrase_beats": "phrase_beats",
    "major_phrase_multiple": "major_phrase_multiple",
    "sample_rate": "sample_rate",
    "hop_length": "hop_length",
    "window_beats": "window_beats",
    "mfcc_count": "mfcc_count",
    "energy_threshold": "energy_change_threshold_db",
    "timbre_threshold": "timbre_change_distance_threshold",
    "max_cues": "max_cues",
    "relative_confidence_threshold": "relative_confidence_threshold",
    "export_csv_path": "export_csv_path",
    "mode": "detection_mode",
    "stems_dir": "stems_dir",
    "verify": "verify",
}


def build_config_from_args(args: argparse.Namespace) -> AppConfig:
    """Build an ``AppConfig``, overriding only the tunables explicitly passed.

    Any tuning flag left as ``None`` (i.e. not supplied on the command
    line) is simply omitted from the ``AppConfig(...)`` call, so it
    cleanly falls back to that field's own dataclass default -- there is
    no duplicated/hardcoded default value here to drift out of sync with
    ``config.py``.

    v1.10: when ``--mode`` is supplied, the preset's bundled thresholds
    override any individual ``--energy-threshold`` / ``--timbre-threshold``
    values.
    """
    overrides: dict[str, Any] = {
        field_name: getattr(args, arg_dest)
        for arg_dest, field_name in _CONFIG_FIELD_BY_ARG_DEST.items()
        if getattr(args, arg_dest) is not None
    }

    # v1.10: --mode preset overrides individual threshold flags
    if args.mode is not None:
        energy, timbre = DETECTION_MODES[args.mode]
        overrides["energy_change_threshold_db"] = energy
        overrides["timbre_change_distance_threshold"] = timbre

    # argparse type=Path produces a Path object; AppConfig expects str | None
    if "export_csv_path" in overrides and overrides["export_csv_path"] is not None:
        overrides["export_csv_path"] = str(overrides["export_csv_path"])
    if "stems_dir" in overrides and overrides["stems_dir"] is not None:
        overrides["stems_dir"] = str(overrides["stems_dir"])

    # cast() tells the type-checker we know the dict values match AppConfig's
    # field types -- the keys are a closed set mapped explicitly above, and
    # every value originates from a typed argparse argument or a literal.
    return AppConfig(**cast(dict[str, Any], overrides))


def _resolve_nml_path(explicit_nml: Path | None) -> Path | None:
    """Return ``explicit_nml`` if given, else the auto-discovered default."""
    if explicit_nml is not None:
        return explicit_nml

    discovered = discover_default_nml_path()
    if discovered is not None:
        logger.info("Auto-discovered collection.nml: %s", discovered)
    return discovered


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    # Validation: --title is only allowed in single-track mode
    if args.title is not None and args.track_path is None:
        print(
            "error: --title is only valid in single-track mode (with TRACK_PATH). "
            "Use --artist to narrow batch title search.",
            file=sys.stderr,
        )
        return 1

    nml_path = _resolve_nml_path(args.nml)
    if nml_path is None:
        print(
            "error: no collection.nml found under the standard Traktor install "
            "directories. Pass --nml PATH explicitly.",
            file=sys.stderr,
        )
        return 1

    config = build_config_from_args(args)

    # Route to single-track or batch pipeline based on selection mode
    if args.track_path is not None:
        # Single-track mode (v1.0)
        try:
            result = run_pipeline(
                nml_path=nml_path,
                track_path=args.track_path,
                config=config,
                title=args.title,
                artist=args.artist,
                clear_existing=args.clear_existing,
            )
        except AmbiguousTrackError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(
                "\nMultiple tracks share this LOCATION. Narrow it down with "
                "--title and/or --artist.",
                file=sys.stderr,
            )
            return 1
        except TrackNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        print(f"{result.entry.artist} - {result.entry.title}")
        print(f"Detected {len(result.detected_events)} event(s):")
        for event in result.detected_events:
            print(
                f"  {event.label:>12}  {event.time_ms:>12.3f} ms  confidence={event.confidence:.3f}"
            )

        if result.written_cues:
            print(
                f"Wrote {len(result.written_cues)} new CUE_V2 element(s) to {nml_path}"
            )
            if config.verify == "smart":
                for cue in result.written_cues:
                    print(f"  [{cue.hotcue}] {cue.name!r} @ {cue.start_ms:.3f} ms")
        else:
            print("No cues written (no confirmed events, or all HOTCUE slots occupied)")

        return 0

    else:
        # Batch mode (v1.1)
        try:
            batch_result = run_batch_pipeline(
                nml_path=nml_path,
                config=config,
                playlist=args.playlist,
                track_title=args.track_title,
                artist=args.artist,
                clear_existing=args.clear_existing,
            )
        except PlaylistNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except AmbiguousPlaylistError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(
                "\nMultiple playlists share this name. Rename one in Traktor.",
                file=sys.stderr,
            )
            return 1
        except TrackNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        # Print per-track results (spec section 8.4 output format)
        for track_result in batch_result.results:
            if track_result.error is not None:
                # Track was skipped
                status = "[skipped]"
                msg = f"{track_result.entry.artist} - {track_result.entry.title}   {track_result.error}"
            else:
                # Track succeeded
                status = "[ok]"
                event_count = (
                    len(track_result.detected_events)
                    if track_result.detected_events
                    else 0
                )
                cue_count = len(track_result.written_cues)
                msg = f"{track_result.entry.artist} - {track_result.entry.title}   {event_count} event(s), {cue_count} cue(s) written"

            print(f"{status:10} {msg}")

        # Print final tally
        total = len(batch_result.results)
        succeeded = batch_result.succeeded_count
        skipped = batch_result.skipped_count
        print(f"\nProcessed {succeeded}/{total} tracks ({skipped} skipped)")

        return 0


if __name__ == "__main__":
    sys.exit(main())
