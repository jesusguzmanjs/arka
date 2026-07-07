"""NML data structures.

Implements the dataclasses described in ``.openspec/2-spec.md``
section 2.3. These are plain data containers with no parsing or file I/O
logic of their own -- see ``nml/parser.py`` for extraction from XML and
(eventually) ``nml/writer.py`` for serialization back to XML.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from traktorco.nml.constants import CueType


@dataclass
class TempoInfo:
    """The ``<TEMPO>`` element of an ``<ENTRY>``."""

    bpm: float
    bpm_quality: float = 100.0


@dataclass
class CuePoint:
    """A single ``<CUE_V2>`` element (spec section 3)."""

    name: str
    type: CueType
    start_ms: float  # milliseconds, matches NML START units
    len_ms: float = 0.0
    repeats: int = -1
    hotcue: int = -1  # -1 = not bound to a Hotcue pad; 0-7 = pad slot
    displ_order: int = 0


@dataclass
class TrackEntry:
    """A parsed ``<ENTRY>`` from ``collection.nml`` (spec section 2.3)."""

    title: str
    artist: str
    location_path: str  # resolved, normalized path used to match the audio file
    tempo: TempoInfo
    cues: list[CuePoint] = field(default_factory=list)
    grid_anchor_ms: float = 0.0  # convenience: START of the TYPE=GRID cue
    duration_ms: float = (
        0.0  # from <INFO PLAYTIME_FLOAT="..."> * 1000; bounds candidate generation
    )
