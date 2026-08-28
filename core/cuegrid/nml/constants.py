"""NML tag/attribute constants.

See ``.openspec/2-spec.md`` section 3.3 for the reverse-engineered
``CueType`` enum, confirmed against Traktor's binary ``TRAKTOR4`` cue
metadata (the same enum is reused in the XML ``TYPE`` attribute of
``<CUE_V2>`` elements).
"""

from __future__ import annotations

from enum import IntEnum


class CueType(IntEnum):
    """The ``TYPE`` attribute of a ``<CUE_V2>`` element."""

    CUE = 0  # Standard Cue Point / HotCue
    FADE_IN = 1
    FADE_OUT = 2
    LOAD = 3
    GRID = 4  # Beatgrid anchor, NAME="AutoGrid"
    LOOP = 5
