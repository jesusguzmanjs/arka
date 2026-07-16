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
import json
import logging
import platform
import sys
from pathlib import Path
from typing import Any, cast, TYPE_CHECKING
if TYPE_CHECKING:
    from cuegrid.core.pipeline import BatchTrackResult

from cuegrid.config import DETECTION_MODES, AppConfig
from cuegrid.audio.loader import generate_preview_payload

from cuegrid.nml.parser import (
    AmbiguousPlaylistError,
    AmbiguousTrackError,
    DuplicateLocationError,
    NmlParser,
    PlaylistNotFoundError,
    TrackNotFoundError,
)
from cuegrid.nml.writer import HotcueNotFoundError, NmlWriter

logger = logging.getLogger(__name__)

# Kept as module attributes so callers/tests can replace the pipeline entry
# points without importing the full analysis stack for metadata/preview-only
# commands. Normal execution resolves them lazily below.
run_pipeline: Any = None
run_batch_pipeline: Any = None
serialize_gui_payload: Any = None

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

    # Mutually exclusive track selection group (spec section 8.4).
    # Not required=True here: --list-playlists (section 12) is a
    # standalone metadata query that needs none of these. main() enforces
    # that exactly one selector is present whenever --list-playlists is
    # NOT given (see the manual check right after parsing).
    selection = parser.add_mutually_exclusive_group(required=False)
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
        "--list-playlists",
        action="store_true",
        default=False,
        dest="list_playlists",
        help=(
            "List every playlist name in the collection as a JSON array on "
            "stdout, then exit immediately. No audio analysis is performed. "
            "Intended for populating a GUI dropdown."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help=(
            "Emit machine-readable NDJSON progress/result messages on "
            "stdout instead of human-readable text. Intended for "
            "non-interactive consumers (e.g. a GUI sidecar)."
        ),
    )
    parser.add_argument(
        "--export-gui",
        action="store_true",
        default=False,
        dest="export_gui",
        help=(
            "Run normal analysis and emit exactly one GUI JSON document on "
            "stdout. Standard logs are redirected to stderr."
        ),
    )
    parser.add_argument(
        "--delete-cue",
        type=int,
        default=None,
        metavar="HOTCUE_INDEX",
        help=(
            "Delete one standard HotCue from TRACK_PATH and persist the "
            "updated collection.nml; HOTCUE_INDEX is zero-based (0-7)."
        ),
    )
    parser.add_argument(
        "--get-track-metadata",
        type=str,
        default=None,
        dest="get_track_metadata",
        metavar="TRACK_PATH",
        help=(
            "Parse the NML and generate the complete low-rate waveform/HPSS "
            "preview Super JSON for TRACK_PATH."
        ),
    )
    parser.add_argument(
        "--get-playlist-tracks",
        type=str,
        default=None,
        dest="get_playlist_tracks",
        metavar="PLAYLIST_NAME",
        help=(
            "Skip audio analysis entirely: parse the NML, locate the "
            "playlist matching PLAYLIST_NAME, and print a JSON array of "
            "{artist, title, location_path, flags, is_flex_grid} objects, one per track "
            "in that playlist, in playlist order, then exit. Intended for "
            "the GUI Library Browser's right-hand tracklist column."
        ),
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
        default="smart",
        help=(
            "Multi-Source Validation mode (v2.2, v1.5). 'smart' (default) "
            "uses the active drum stem and cross-checks candidates against "
            "the Master audio; 'fast' skips that classification pass. Both "
            "modes fall back to the Master if the drum stem is empty/ambient."
        ),
    )
    parser.add_argument(
        "--no-stems",
        action="store_true",
        default=False,
        dest="no_stems",
        help=(
            "Force standard Master audio analysis. Bypasses native Stem "
            "lookup, FLAGS bitmask evaluation, and .stem.mp4 extraction "
            "completely."
        ),
    )

    parser.add_argument(
            "--discover-nml",
            action="store_true",
            help="Standalone query: discover the default collection.nml, print its path as JSON, and exit.",
    )

    parser.add_argument(
            "--update-cues",
            type=str,
            default=None,
            metavar="JSON_STRING",
            help="JSON string arrays of options [{'hotcue': x, 'start_ms': y}] to overwrite on TRACK_PATH",
    )
    parser.add_argument(
        "--grid-anchor",
        type=float,
        default=None,
        metavar="MS",
        help=(
            "Optional Grid marker START position in milliseconds. Valid only "
            "with --update-cues and rejected for Flex Grid tracks."
        ),
    )
    parser.add_argument(
        "--bpm",
        type=float,
        default=None,
        metavar="BPM",
        help=(
            "Optional track BPM to persist in the matched ENTRY. Valid only "
            "with --update-cues and limited to the inclusive range 50-200."
        ),
    )
    parser.add_argument(
        "--get-library",
        action="store_true",
        default=False,
        dest="get_library",
        help=(
            "Parse the complete collection and playlist tree into one compact "
            "relational JSON payload, then exit without audio analysis."
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
            "Dynamic sensitivity preset: 'soft' (energy=2.0, timbre=8.0, "
            "relative-confidence=0.15), 'medium' (energy=4.0, timbre=18.0, "
            "relative-confidence=0.30, default), 'hard' (energy=7.0, "
            "timbre=30.0, relative-confidence=0.50). Overrides all three "
            "threshold flags when set."
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
    "no_stems": "no_stems",
}


def build_config_from_args(args: argparse.Namespace) -> AppConfig:
    """Build an ``AppConfig``, overriding only the tunables explicitly passed.

    Any tuning flag left as ``None`` (i.e. not supplied on the command
    line) is simply omitted from the ``AppConfig(...)`` call, so it
    cleanly falls back to that field's own dataclass default -- there is
    no duplicated/hardcoded default value here to drift out of sync with
    ``config.py``.

    v1.4: when ``--mode`` is supplied, the preset's bundled energy, timbre,
    and relative-confidence thresholds override any individual threshold
    flags.
    """
    overrides: dict[str, Any] = {
        field_name: getattr(args, arg_dest)
        for arg_dest, field_name in _CONFIG_FIELD_BY_ARG_DEST.items()
        if getattr(args, arg_dest) is not None
    }

    # v1.4: --mode preset overrides all three threshold flags
    if args.mode is not None:
        energy, timbre, relative_confidence = DETECTION_MODES[args.mode]
        overrides["energy_change_threshold_db"] = energy
        overrides["timbre_change_distance_threshold"] = timbre
        overrides["relative_confidence_threshold"] = relative_confidence

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


# ---------------------------------------------------------------------------
# v2.3 NDJSON helpers (spec section 11)
# ---------------------------------------------------------------------------


def _emit_json(obj: dict[str, Any]) -> None:
    """Write a single-line JSON object to stdout, flushed immediately."""
    print(json.dumps(obj, separators=(",", ":")), flush=True)


def _json_log(level: str, message: str) -> None:
    _emit_json({"type": "log", "level": level, "message": message})


def _json_nml_resolved(path: Path) -> None:
    _emit_json({"type": "nml_resolved", "path": str(path)})


def _json_track_start(index: int, total: int, artist: str, title: str) -> None:
    _emit_json(
        {"type": "track_start", "index": index, "total": total,
         "artist": artist, "title": title}
    )


def _json_event_detected(ev: Any) -> None:
    _emit_json({
        "type": "event_detected",
        "label": ev.label,
        "time_ms": ev.time_ms,
        "confidence": ev.confidence,
        "is_major_phrase": ev.is_major_phrase,
    })


def _json_cue_written(cue: Any) -> None:
    _emit_json({
        "type": "cue_written",
        "hotcue": cue.hotcue,
        "name": cue.name,
        "start_ms": cue.start_ms,
    })


def _json_track_complete(
    artist: str, title: str,
    event_count: int, cue_count: int,
    error: str | None,
) -> None:
    _emit_json({
        "type": "track_complete",
        "artist": artist,
        "title": title,
        "event_count": event_count,
        "cue_count": cue_count,
        "error": error,
    })


def _json_summary(total: int, succeeded: int, skipped: int) -> None:
    _emit_json(
        {"type": "summary", "total": total, "succeeded": succeeded,
         "skipped": skipped}
    )


def _json_skipped(reason: str) -> None:
    """Emit the compact protected-skip event consumed by the GUI."""
    _emit_json({"type": "skipped", "reason": reason})


def _emit_track_lifecycle_json(
    index: int,
    total: int,
    artist: str,
    title: str,
    detected_events: list[Any] | None,
    written_cues: list[Any],
    error: str | None,
) -> None:
    """Emit track_start, event_detected*, cue_written*, track_complete for one track.

    Shared between single-track mode (index=1, total=1) and batch mode
    (invoked per ``BatchTrackResult`` via ``_json_batch_track_callback``),
    per spec section 11.4's mapping table.
    """
    _json_track_start(index, total, artist, title)
    if detected_events:
        for event in detected_events:
            _json_event_detected(event)
    for cue in written_cues:
        _json_cue_written(cue)
    event_count = len(detected_events) if detected_events else 0
    cue_count = len(written_cues)
    _json_track_complete(artist, title, event_count, cue_count, error)


def _json_batch_track_callback(track_result: BatchTrackResult) -> None:
    """``on_track_complete`` callback: streams one track's JSON lifecycle.

    Passed to ``run_batch_pipeline`` when ``--json`` is set, so each
    track's messages are emitted as soon as that track finishes,
    rather than only after the whole batch returns (spec section 11.4).
    """
    if track_result.error == "flex_grid":
        _json_skipped("flex_grid")
    _emit_track_lifecycle_json(
        index=track_result.index,
        total=track_result.total,
        artist=track_result.entry.artist,
        title=track_result.entry.title,
        detected_events=track_result.detected_events,
        written_cues=track_result.written_cues,
        error=track_result.error,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.grid_anchor is not None and args.update_cues is None:
        build_parser().error("--grid-anchor may only be used with --update-cues")
    if args.bpm is not None and args.update_cues is None:
        build_parser().error("--bpm may only be used with --update-cues")

    # Configure this mode before any path discovery so a pre-existing logging
    # configuration cannot leak INFO/DEBUG text onto the GUI stdout stream.
    if args.export_gui:
        logging.basicConfig(
            level=logging.WARNING,
            format="%(levelname)s: %(message)s",
            stream=sys.stderr,
            force=True,
        )


    # --update-cues: operación de actualización manual desde el grid
    if args.update_cues is not None:
        if args.track_path is None:
            print("error: --update-cues requires TRACK_PATH.", file=sys.stderr)
            return 1
            
        nml_path = _resolve_nml_path(args.nml)
        if nml_path is None:
            print("error: no collection.nml found.", file=sys.stderr)
            return 1
            
        try:
            cues_list = json.loads(args.update_cues)
            
            # Aquí invocas la lógica de tu NmlWriter (debes implementar 'update_track_hotcues' en tu escritor)
            NmlWriter(NmlParser(nml_path)).update_track_hotcues(
                args.track_path,
                cues_list,
                title=args.title,
                artist=args.artist,
                grid_anchor_ms=args.grid_anchor,
                bpm=args.bpm,
            )
            print(f"Successfully updated manual cues for {args.track_path}")
            return 0
        except Exception as exc:
            print(f"error: failed to update hotcues: {exc}", file=sys.stderr)
            return 1

    # --list-playlists (spec section 12.3): a standalone, lightning-fast
    # metadata query for a future GUI dropdown. Intercepted before
    # logging.basicConfig, --title validation, or any audio/pipeline
    # code runs -- no logs, no librosa, no tracking logic.
    if args.list_playlists:
        nml_path = _resolve_nml_path(args.nml)
        if nml_path is None:
            print(
                "error: no collection.nml found under the standard Traktor "
                "install directories. Pass --nml PATH explicitly.",
                file=sys.stderr,
            )
            return 1
        names = NmlParser(nml_path).list_playlist_names()
        print(json.dumps(names))
        sys.exit(0)

    # --get-library: one standalone relational query for the Global Collection
    # browser. The parser indexes the master collection once and emits playlist
    # references only; no audio or pipeline code is involved.
    if args.get_library:
        nml_path = _resolve_nml_path(args.nml)
        if nml_path is None:
            print(
                "error: no collection.nml found under the standard Traktor "
                "install directories. Pass --nml PATH explicitly.",
                file=sys.stderr,
            )
            return 1
        try:
            payload = NmlParser(nml_path).get_library()
        except DuplicateLocationError as exc:
            print(
                json.dumps(
                    {"error": "duplicate_location", "message": str(exc)},
                    separators=(",", ":"),
                )
            )
            return 1
        print(json.dumps(payload, separators=(",", ":")))
        sys.exit(0)

    # --delete-cue (spec .openspec/2-core-spec.md section 13): a
    # standalone destructive operation. It must run before normal selector
    # validation and never enters the audio pipeline.
    if args.delete_cue is not None:
        if args.track_path is None:
            print(
                "error: --delete-cue requires TRACK_PATH as the track identifier.",
                file=sys.stderr,
            )
            return 1
        if args.track_title is not None or args.playlist is not None:
            print(
                "error: --delete-cue cannot be combined with --track-title or --playlist.",
                file=sys.stderr,
            )
            return 1
        if args.delete_cue < 0 or args.delete_cue > 7:
            print("error: HOTCUE_INDEX must be between 0 and 7.", file=sys.stderr)
            return 1
        nml_path = _resolve_nml_path(args.nml)
        if nml_path is None:
            print(
                "error: no collection.nml found under the standard Traktor "
                "install directories. Pass --nml PATH explicitly.",
                file=sys.stderr,
            )
            return 1
        try:
            NmlWriter(NmlParser(nml_path)).delete_cue(
                args.track_path,
                args.delete_cue,
                title=args.title,
                artist=args.artist,
            )
        except (TrackNotFoundError, AmbiguousTrackError, HotcueNotFoundError, ValueError, OSError) as exc:
            print(f"error: failed to delete HotCue: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            # XML parse and other filesystem/library failures must still be
            # reported as a non-zero destructive-operation failure.
            print(f"error: failed to delete HotCue: {exc}", file=sys.stderr)
            return 1
        print(
            f'Deleted HotCue {args.delete_cue} from "{args.track_path}" in {nml_path}'
        )
        return 0

    # --discover-nml: a standalone query to feed the GUI's initial state
    if args.discover_nml:
        discovered = discover_default_nml_path()
        if discovered:
            print(json.dumps({"path": str(discovered)}))
            return 0
        else:
            print(json.dumps({"error": "No default Traktor collection found"}))
            return 1

    # --get-track-metadata (spec .openspec/3-player-spec.md section 1.2): a
    # standalone, one-shot preview query for the GUI's waveform player.
    # Intercepted before logging.basicConfig and the normal pipeline. Errors are reported
    # as JSON on stdout (not stderr), matching this flag's own error
    # schema (section 1.4).
    if args.get_track_metadata is not None:
        nml_path = _resolve_nml_path(args.nml)
        if nml_path is None:
            print(
                "error: no collection.nml found under the standard Traktor "
                "install directories. Pass --nml PATH explicitly.",
                file=sys.stderr,
            )
            return 1
        try:
            metadata = NmlParser(nml_path).get_track_metadata(
                args.get_track_metadata, title=args.title, artist=args.artist
            )
        except TrackNotFoundError as exc:
            print(json.dumps({"error": "not_found", "message": str(exc)}))
            sys.exit(1)
        except AmbiguousTrackError as exc:
            print(
                json.dumps(
                    {
                        "error": "ambiguous",
                        "message": (
                            f"{exc} Multiple tracks share this LOCATION. "
                            "Narrow it down with --title and/or --artist."
                        ),
                    }
                )
            )
            sys.exit(1)
        try:
            waveform_peaks, color_map = generate_preview_payload(args.get_track_metadata)
        except Exception as exc:
            print(json.dumps({"error": "preview_failed", "message": str(exc)}))
            sys.exit(1)

        metadata["waveform_peaks"] = waveform_peaks
        metadata["color_map"] = color_map
        print(json.dumps(metadata, separators=(",", ":")))
        sys.exit(0)

    # --get-playlist-tracks (spec .openspec/4-library-spec.md section 1.2): a
    # standalone, one-shot tracklist query for the GUI Library Browser's
    # right-hand column. Intercepted before logging.basicConfig, --title
    # validation, or any audio/pipeline code runs -- no logs, no librosa,
    # no tracking logic. Errors are reported as JSON on stdout (not
    # stderr), matching this flag's own error schema (section 1.4).
    if args.get_playlist_tracks is not None:
        nml_path = _resolve_nml_path(args.nml)
        if nml_path is None:
            print(
                "error: no collection.nml found under the standard Traktor "
                "install directories. Pass --nml PATH explicitly.",
                file=sys.stderr,
            )
            return 1
        try:
            refs = NmlParser(nml_path).find_entries_by_playlist(
                args.get_playlist_tracks
            )
        except PlaylistNotFoundError as exc:
            print(json.dumps({"error": "not_found", "message": str(exc)}))
            sys.exit(1)
        except AmbiguousPlaylistError as exc:
            print(
                json.dumps(
                    {
                        "error": "ambiguous",
                        "message": (
                            f"{exc} Rename one of the playlists in Traktor "
                            "to disambiguate."
                        ),
                    }
                )
            )
            sys.exit(1)
        tracks = [
            {
                "artist": ref.entry.artist,
                "title": ref.entry.title,
                "location_path": ref.entry.location_path,
                "flags": int(ref.entry.flags) if ref.entry.flags is not None else None,
                "is_flex_grid": ref.entry.is_flex_grid,
            }
            for ref in refs
        ]
        print(json.dumps(tracks))
        sys.exit(0)

    if args.export_gui and args.json:
        print("error: --export-gui cannot be combined with --json", file=sys.stderr)
        return 1

    use_json = args.json

    logging.basicConfig(
        level=logging.INFO if args.verbose and not args.export_gui else logging.WARNING,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
        force=args.export_gui,
    )

    # Validation: exactly one of track_path/--track-title/--playlist is
    # required (spec section 8.4) -- enforced manually now that the
    # argparse group itself is not required=True (section 12.1).
    selectors_given = sum(
        1
        for v in (args.track_path, args.track_title, args.playlist)
        if v is not None
    )
    if selectors_given != 1:
        message = (
            "exactly one of TRACK_PATH, --track-title, or --playlist is required"
        )
        if use_json:
            _json_log("error", message)
        else:
            print(f"error: {message}", file=sys.stderr)
        return 1

    if args.export_gui and args.track_path is None:
        print(
            "error: --export-gui requires TRACK_PATH and cannot be used with "
            "--track-title or --playlist",
            file=sys.stderr,
        )
        return 1

    # Validation: --title is only allowed in single-track mode
    if args.title is not None and args.track_path is None:
        message = (
            "--title is only valid in single-track mode (with TRACK_PATH). "
            "Use --artist to narrow batch title search."
        )
        if use_json:
            _json_log("error", message)
        else:
            print(f"error: {message}", file=sys.stderr)
        return 1

    nml_path = _resolve_nml_path(args.nml)
    if nml_path is None:
        message = (
            "no collection.nml found under the standard Traktor install "
            "directories. Pass --nml PATH explicitly."
        )
        if use_json:
            _json_log("error", message)
        else:
            print(f"error: {message}", file=sys.stderr)
        return 1

    if use_json:
        _json_nml_resolved(nml_path)

    config = build_config_from_args(args)

    # Route to single-track or batch pipeline based on selection mode
    if args.track_path is not None:
        global run_pipeline, serialize_gui_payload
        if run_pipeline is None:
            from cuegrid.core.pipeline import run_pipeline as pipeline_run
            run_pipeline = pipeline_run
        if serialize_gui_payload is None:
            from cuegrid.core.pipeline import serialize_gui_payload as pipeline_serialize
            serialize_gui_payload = pipeline_serialize
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
            if use_json:
                _json_log("error", str(exc))
                _json_log(
                    "error",
                    "Multiple tracks share this LOCATION. Narrow it down with "
                    "--title and/or --artist.",
                )
            else:
                print(f"error: {exc}", file=sys.stderr)
                print(
                    "\nMultiple tracks share this LOCATION. Narrow it down with "
                    "--title and/or --artist.",
                    file=sys.stderr,
                )
            return 1
        except TrackNotFoundError as exc:
            if use_json:
                _json_log("error", str(exc))
            else:
                print(f"error: {exc}", file=sys.stderr)
            return 1

        if args.export_gui:
            # This is the only stdout write in export mode: one raw JSON
            # document for the Tauri in-memory bridge.
            print(serialize_gui_payload(result, args.track_path), flush=True)
            return 0

        if result.skipped_reason is not None:
            if use_json:
                _json_skipped(result.skipped_reason)
                _emit_track_lifecycle_json(
                    index=1,
                    total=1,
                    artist=result.entry.artist,
                    title=result.entry.title,
                    detected_events=[],
                    written_cues=[],
                    error=result.skipped_reason,
                )
                _json_summary(total=1, succeeded=0, skipped=1)
            else:
                print(
                    f"[skipped] {result.entry.artist} - {result.entry.title}   "
                    "Flex Grid / variable BPM unsupported"
                )
            return 0

        if use_json:
            _emit_track_lifecycle_json(
                index=1,
                total=1,
                artist=result.entry.artist,
                title=result.entry.title,
                detected_events=result.detected_events,
                written_cues=result.written_cues,
                error=None,
            )
            _json_summary(total=1, succeeded=1, skipped=0)
            return 0

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
        global run_batch_pipeline
        if run_batch_pipeline is None:
            from cuegrid.core.pipeline import run_batch_pipeline as pipeline_run_batch
            run_batch_pipeline = pipeline_run_batch
        try:
            batch_result = run_batch_pipeline(
                nml_path=nml_path,
                config=config,
                playlist=args.playlist,
                track_title=args.track_title,
                artist=args.artist,
                clear_existing=args.clear_existing,
                on_track_complete=_json_batch_track_callback if use_json else None,
            )
        except PlaylistNotFoundError as exc:
            if use_json:
                _json_log("error", str(exc))
            else:
                print(f"error: {exc}", file=sys.stderr)
            return 1
        except AmbiguousPlaylistError as exc:
            if use_json:
                _json_log("error", str(exc))
                _json_log(
                    "error", "Multiple playlists share this name. Rename one in Traktor."
                )
            else:
                print(f"error: {exc}", file=sys.stderr)
                print(
                    "\nMultiple playlists share this name. Rename one in Traktor.",
                    file=sys.stderr,
                )
            return 1
        except TrackNotFoundError as exc:
            if use_json:
                _json_log("error", str(exc))
            else:
                print(f"error: {exc}", file=sys.stderr)
            return 1

        total = len(batch_result.results)
        succeeded = batch_result.succeeded_count
        skipped = batch_result.skipped_count

        if use_json:
            # Per-track messages were already streamed via
            # _json_batch_track_callback as each track completed
            # (spec section 11.4); only the final summary remains.
            _json_summary(total=total, succeeded=succeeded, skipped=skipped)
            return 0

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
        print(f"\nProcessed {succeeded}/{total} tracks ({skipped} skipped)")

        return 0


if __name__ == "__main__":
    sys.exit(main())
