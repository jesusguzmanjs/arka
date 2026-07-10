"""Telemetry cache storage for the most recent analysis execution."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

TELEMETRY_FIELDNAMES = [
    "track_title",
    "beat",
    "time_ms",
    "energy_delta_db",
    "timbre_dist",
    "confidence",
    "status",
    "track_peak_db",
    "track_perceived_db",
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


def _write_rows(rows: Iterable[Mapping[str, object]], mode: str) -> None:
    with TELEMETRY_CACHE_PATH.open(mode, newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TELEMETRY_FIELDNAMES)
        if mode == "w":
            writer.writeheader()
        writer.writerows(rows)
    os.chmod(TELEMETRY_CACHE_PATH, 0o600)
