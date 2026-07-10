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

from cuegrid.audio.detector import DetectedEvent
from cuegrid.nml.constants import CueType
from cuegrid.nml.models import CuePoint

logger = logging.getLogger(__name__)

_MAX_HOTCUE_SLOTS = 8  # Traktor supports 8 hotcue pads per deck (spec section 3.2)

# Two cues within this many milliseconds of each other are considered the
# same timeline position -- a detected event this close to an existing cue
# (manual or from a previous run) is redundant and must be skipped rather
# than assigned to a different HOTCUE slot at (essentially) the same time.
TOLERANCE_MS = 50.0

# Display name for the single, unified structural cue label (spec section
# 6.1, v1.4) -- there are no more position-based intro/drop/outro roles.
_LABEL_TO_NAME = {
    "cue": "Cue",
}


def _lowest_free_slot(occupied_slots: set[int]) -> int | None:
    """Return the lowest hotcue slot in ``0..7`` not in ``occupied_slots``."""
    for slot in range(_MAX_HOTCUE_SLOTS):
        if slot not in occupied_slots:
            return slot
    return None


def map_event_to_cue(event: DetectedEvent, occupied_slots: set[int]) -> CuePoint | None:
    """Map a single ``DetectedEvent`` to a ``CuePoint``, or ``None`` if no slot is free."""
    slot = _lowest_free_slot(occupied_slots)
    if slot is None:
        return None  # Ahora es silenciosa, solo devuelve None si no hay sitio

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
    events: list[DetectedEvent],
    existing_cues: list[CuePoint],
    clear_existing: bool = False,
) -> list[CuePoint]:
    """Map every confirmed ``DetectedEvent`` to a ``CuePoint``, in chronological order.

    Args:
        events: Confirmed events from ``audio.detector.detect_events``.
        existing_cues: The ``ENTRY``'s current ``CuePoint``s (from
            ``TrackEntry.cues``), used to seed the set of occupied HOTCUE
            slots so this project never overwrites a slot the user (or a
            previous run of this tool) already assigned (spec section 3.4).
        clear_existing: If ``True``, ignore existing standard HotCues
            (``TYPE="0"``) when calculating occupied slots, treating those
            slots as free. Grid/BPM (``TYPE="4"``) and Load (``TYPE="3"``)
            markers are still considered occupied because the writer
            preserves them.

    Returns:
        The new ``CuePoint``s to append, one per event that could be
        assigned a free slot -- events skipped due to slot exhaustion are
        simply absent from the result (logs a single unified warning per track
        if slots are exhausted).
    """
    occupied_slots: set[int] = set()
    existing_times: list[float] = []
    for cue in existing_cues:
        is_being_cleared = clear_existing and cue.type == CueType.CUE
        if not is_being_cleared:
            # Track the timestamp so new events too close to it are treated
            # as redundant, regardless of whether this cue is bound to a
            # HOTCUE slot (spec section 3.4 addendum: no two cues -- of any
            # kind -- should trigger at effectively the same instant).
            existing_times.append(cue.start_ms)
        if cue.hotcue == -1:
            continue
        if is_being_cleared:
            continue  # standard HotCues will be cleared by the writer
        occupied_slots.add(cue.hotcue)

    # ─── INSTANTÁNEA: Calculamos el estado PREVIO antes de escribir ───
    initial_occupied = sorted([s + 1 for s in occupied_slots])
    initial_count = len(initial_occupied)

    if initial_count > 0:
        initial_slots_str = ", ".join(f"HOTCUE {s}" for s in initial_occupied)
        warn_msg = (
            f"HOTCUE slots filled up (8/8); skipping remaining events. "
            f"Track initially had {initial_count} occupied slot(s): {initial_slots_str}."
        )
    else:
        warn_msg = (
            "HOTCUE slots filled up (8/8); skipping remaining events. "
            "Track initially had 0 occupied slots."
        )

    # ─── BUCLE DE PROCESAMIENTO ───
    new_cues: list[CuePoint] = []
    has_warned_full = False

    for event in sorted(events, key=lambda e: e.time_ms):
        if any(abs(event.time_ms - t) <= TOLERANCE_MS for t in existing_times):
            logger.debug(
                "Skipping event at %.3fms: redundant with an existing cue "
                "within %.1fms.",
                event.time_ms,
                TOLERANCE_MS,
            )
            continue  # already have a cue at (essentially) this instant

        cue = map_event_to_cue(event, occupied_slots)

        if cue is None:
            if not has_warned_full:
                # Lanzamos el mensaje personalizado que guardamos al principio
                logger.warning(warn_msg)
                has_warned_full = True
            continue

        new_cues.append(cue)
        occupied_slots.add(cue.hotcue)
        existing_times.append(cue.start_ms)

    return new_cues
