"""Telemetry cache storage for the most recent analysis execution."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

TELEMETRY_FIELDNAMES = [
    "track_title",
    "Formatted_Time",
    "beat",
    "time_ms",
    "energy_delta_db",
    "timbre_dist",
    "confidence",
    "status",
    "track_peak_db",
    "track_perceived_db",
    "Drum_Score",
    "Drum_Weight_Applied",
]

# Keep the cache in an application-owned directory below the OS temporary
# directory. Both the Python sidecar and the Tauri host use this stable path.
TELEMETRY_CACHE_DIR = Path(tempfile.gettempdir()) / "cuegrid"
TELEMETRY_CACHE_PATH = TELEMETRY_CACHE_DIR / "last_run_telemetry.csv"


def reset_telemetry_cache() -> None:
    """Replace the cache with a fresh header for a new execution."""
    TELEMETRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _write_rows((), mode="w")


def append_telemetry_rows(rows: Iterable[Mapping[str, object]]) -> None:
    """Append rows from one track to the current execution's cache."""
    TELEMETRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _write_rows(rows, mode="a")


def format_timestamp_ms(time_ms: float) -> str:
    """Format milliseconds as ``MM:SS.mmm`` for human-readable telemetry."""
    total_ms = max(0, int(round(time_ms)))
    total_minutes, remainder_ms = divmod(total_ms, 60_000)
    seconds, milliseconds = divmod(remainder_ms, 1_000)
    return f"{total_minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def update_telemetry_rows(
    csv_path: str | Path,
    track_title: str,
    metrics_by_time_ms: Mapping[float, Mapping[str, object]],
) -> None:
    """Update Smart Mode fields on rows already written for one track.

    Fusion metrics can be patched by their stable
    ``track_title``/``time_ms`` identity when a caller emits telemetry before
    the aligned Drum envelope is available.
    """
    path = Path(csv_path)
    if not metrics_by_time_ms or not path.exists():
        return

    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        if row.get("track_title") != track_title:
            continue
        try:
            time_ms = float(row["time_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        metrics = metrics_by_time_ms.get(time_ms)
        if metrics is None:
            continue
        row["Drum_Score"] = str(metrics.get("Drum_Score", "N/A"))
        row["Drum_Weight_Applied"] = str(
            metrics.get("Drum_Weight_Applied", "0.0")
        )

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=TELEMETRY_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _write_rows(rows: Iterable[Mapping[str, object]], mode: str) -> None:
    with TELEMETRY_CACHE_PATH.open(mode, newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TELEMETRY_FIELDNAMES)
        if mode == "w":
            writer.writeheader()
        writer.writerows(rows)
    os.chmod(TELEMETRY_CACHE_PATH, 0o600)
