"""Map ``DetectedEvent``s to Traktor ``CuePoint``s.

Implements the ``core.mapping`` responsibility from ``.openspec/2-spec.md``
section 2.1: decide the ``CueType`` and free ``HOTCUE`` slot for each
detected structural event. Per section 3.3, this project only ever writes
``CueType.CUE`` (``0``); per section 3.4, the lowest free slot in ``0..7``
is assigned, existing slots are never overwritten, and an event is skipped
(with a logged warning, never a crash) if all 8 slots are already taken.

This module never reads or writes files (spec section 2.1) -- it is a
pure function of the ``DetectedEvent``s and the existing ``CuePoint``s
already on the track.
"""

from __future__ import annotations

import logging

from traktorco.audio.detector import DetectedEvent
from traktorco.nml.constants import CueType
from traktorco.nml.models import CuePoint

logger = logging.getLogger(__name__)

_MAX_HOTCUE_SLOTS = 8  # Traktor supports 8 hotcue pads per deck (spec section 3.2)

# Display names matching the illustrative <CUE_V2> examples in spec section 3.1.
_LABEL_TO_NAME = {
    "intro_end": "Intro End",
    "drop": "Drop",
    "outro_start": "Outro",
}


def _lowest_free_slot(occupied_slots: set[int]) -> int | None:
    """Return the lowest hotcue slot in ``0..7`` not in ``occupied_slots``."""
    for slot in range(_MAX_HOTCUE_SLOTS):
        if slot not in occupied_slots:
            return slot
    return None


def map_event_to_cue(event: DetectedEvent, occupied_slots: set[int]) -> CuePoint | None:
    """Map a single ``DetectedEvent`` to a ``CuePoint``, or ``None`` if no slot is free.

    Args:
        event: The confirmed structural event to map.
        occupied_slots: HOTCUE slots (``0..7``) already in use on the
            target ``ENTRY`` -- must include both pre-existing cues and
            any already assigned earlier in this same mapping run.

    Returns:
        A new ``CuePoint`` (always ``type=CueType.CUE``, per spec section
        3.3), or ``None`` if all 8 slots are occupied. Callers must treat
        ``None`` as "skip this event", not as an error (spec section 3.4).
    """
    slot = _lowest_free_slot(occupied_slots)
    if slot is None:
        logger.warning(
            "All 8 HOTCUE slots are occupied; skipping %r event at %.3fms",
            event.label,
            event.time_ms,
        )
        return None

    return CuePoint(
        name=_LABEL_TO_NAME.get(event.label, event.label),
        type=CueType.CUE,
        start_ms=event.time_ms,
        len_ms=0.0,
        repeats=-1,
        hotcue=slot,
        displ_order=0,
    )


def map_events_to_cues(
    events: list[DetectedEvent], existing_cues: list[CuePoint]
) -> list[CuePoint]:
    """Map every confirmed ``DetectedEvent`` to a ``CuePoint``, in chronological order.

    Args:
        events: Confirmed events from ``audio.detector.detect_events``.
        existing_cues: The ``ENTRY``'s current ``CuePoint``s (from
            ``TrackEntry.cues``), used to seed the set of occupied HOTCUE
            slots so this project never overwrites a slot the user (or a
            previous run of this tool) already assigned (spec section 3.4).

    Returns:
        The new ``CuePoint``s to append, one per event that could be
        assigned a free slot -- events skipped due to slot exhaustion are
        simply absent from the result (already logged as a warning by
        ``map_event_to_cue``).
    """
    occupied_slots = {cue.hotcue for cue in existing_cues if cue.hotcue != -1}

    new_cues: list[CuePoint] = []
    for event in sorted(events, key=lambda e: e.time_ms):
        cue = map_event_to_cue(event, occupied_slots)
        if cue is None:
            continue
        new_cues.append(cue)
        occupied_slots.add(cue.hotcue)

    return new_cues
