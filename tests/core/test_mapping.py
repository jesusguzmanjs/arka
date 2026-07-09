"""Tests for ``traktorco.core.mapping``.

Covers the mapping policy from ``.openspec/2-spec.md`` sections 2.1 and
3.4: DetectedEvent -> CueType.CUE, lowest free HOTCUE slot in 0..7, never
overwriting existing slots, and skipping (with a warning, not a crash)
once all 8 slots are occupied.
"""

from __future__ import annotations

import logging

import pytest

from traktorco.audio.detector import DetectedEvent
from traktorco.core.mapping import map_event_to_cue, map_events_to_cues
from traktorco.nml.constants import CueType
from traktorco.nml.models import CuePoint


def _event(
    label: str, time_ms: float, beat_index: int = 0, confidence: float = 1.0
) -> DetectedEvent:
    return DetectedEvent(
        label=label,
        time_ms=time_ms,
        beat_index=beat_index,
        is_major_phrase=False,
        confidence=confidence,
    )


class TestMapEventToCue:
    def test_maps_to_cue_type_cue(self):
        cue = map_event_to_cue(_event("cue", 1000.0), occupied_slots=set())
        assert cue is not None
        assert cue.type == CueType.CUE

    def test_carries_timestamp_through_unchanged(self):
        cue = map_event_to_cue(_event("cue", 6997.217), occupied_slots=set())
        assert cue is not None
        assert cue.start_ms == pytest.approx(6997.217)

    def test_point_cue_defaults(self):
        cue = map_event_to_cue(_event("cue", 5000.0), occupied_slots=set())
        assert cue is not None
        assert cue.len_ms == 0.0
        assert cue.repeats == -1

    def test_assigns_lowest_free_slot(self):
        cue = map_event_to_cue(_event("cue", 1000.0), occupied_slots={0, 1, 3})
        assert cue is not None
        assert cue.hotcue == 2

    def test_assigns_slot_zero_when_nothing_occupied(self):
        cue = map_event_to_cue(_event("cue", 1000.0), occupied_slots=set())
        assert cue is not None
        assert cue.hotcue == 0

    def test_returns_none_when_all_eight_slots_occupied(self, caplog):
        with caplog.at_level(logging.WARNING):
            cue = map_event_to_cue(_event("cue", 1000.0), occupied_slots=set(range(8)))
        assert cue is None
        assert "occupied" in caplog.text.lower()

    def test_never_raises_on_slot_exhaustion(self):
        # Must be a silent (logged) skip, never an exception (spec 3.4).
        try:
            map_event_to_cue(_event("cue", 1000.0), occupied_slots=set(range(8)))
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"map_event_to_cue raised unexpectedly: {exc!r}")


class TestMapEventsToCues:
    def test_assigns_increasing_free_slots_in_chronological_order(self):
        events = [
            _event("cue", 20000.0),
            _event("cue", 1000.0),
            _event("cue", 50000.0),
        ]
        cues = map_events_to_cues(events, existing_cues=[])

        assert [c.start_ms for c in cues] == [1000.0, 20000.0, 50000.0]
        assert [c.hotcue for c in cues] == [0, 1, 2]

    def test_skips_slots_already_used_by_existing_cues(self):
        existing = [
            CuePoint(name="AutoGrid", type=CueType.GRID, start_ms=10.0, hotcue=-1),
            CuePoint(name="Manual Cue", type=CueType.CUE, start_ms=500.0, hotcue=0),
        ]
        events = [_event("cue", 1000.0)]

        cues = map_events_to_cues(events, existing_cues=existing)

        assert cues[0].hotcue == 1  # slot 0 is already taken by "Manual Cue"

    def test_grid_cues_unbound_slot_is_never_treated_as_occupied(self):
        # AutoGrid's HOTCUE=-1 (unbound) must not consume a real slot.
        existing = [
            CuePoint(name="AutoGrid", type=CueType.GRID, start_ms=10.0, hotcue=-1)
        ]
        cues = map_events_to_cues([_event("cue", 1000.0)], existing_cues=existing)
        assert cues[0].hotcue == 0

    def test_stops_producing_cues_once_slots_are_exhausted(self):
        existing = [
            CuePoint(
                name=f"Existing {i}", type=CueType.CUE, start_ms=float(i), hotcue=i
            )
            for i in range(7)
        ]
        events = [_event("cue", 1000.0), _event("cue", 2000.0)]

        cues = map_events_to_cues(events, existing_cues=existing)

        # Only 1 free slot (7) was available; the second event is skipped.
        assert len(cues) == 1
        assert cues[0].hotcue == 7

    def test_empty_events_produces_empty_cues(self):
        assert map_events_to_cues([], existing_cues=[]) == []

    def test_clear_existing_ignores_type_0_cues(self):
        """When clear_existing=True, TYPE="0" (CUE) slots are treated as free."""
        existing = [
            CuePoint(name="User Cue 1", type=CueType.CUE, start_ms=1000.0, hotcue=0),
            CuePoint(name="User Cue 2", type=CueType.CUE, start_ms=2000.0, hotcue=1),
            CuePoint(name="User Cue 3", type=CueType.CUE, start_ms=3000.0, hotcue=2),
        ]
        events = [_event("cue", 1000.0), _event("cue", 2000.0), _event("cue", 3000.0)]

        cues = map_events_to_cues(events, existing_cues=existing, clear_existing=True)

        # All three TYPE="0" slots are ignored, so we start filling from slot 0.
        assert [c.hotcue for c in cues] == [0, 1, 2]

    def test_clear_existing_still_respects_type_4_grid_cues(self):
        """When clear_existing=True, TYPE="4" (GRID) slots remain occupied."""
        existing = [
            CuePoint(name="AutoGrid", type=CueType.GRID, start_ms=10.0, hotcue=0),
            CuePoint(name="User Cue", type=CueType.CUE, start_ms=500.0, hotcue=1),
        ]
        events = [_event("cue", 1000.0), _event("cue", 2000.0)]

        cues = map_events_to_cues(events, existing_cues=existing, clear_existing=True)

        # Slot 0 (GRID) is still occupied; slot 1 (CUE) is freed.
        # So we fill slot 1 first, then slot 2.
        assert cues[0].hotcue == 1
        assert cues[1].hotcue == 2

    def test_clear_existing_still_respects_type_3_load_cues(self):
        """When clear_existing=True, TYPE="3" (LOAD) slots remain occupied."""
        existing = [
            CuePoint(name="Load", type=CueType.LOAD, start_ms=0.0, hotcue=0),
            CuePoint(name="User Cue", type=CueType.CUE, start_ms=500.0, hotcue=1),
        ]
        events = [_event("cue", 1000.0), _event("cue", 2000.0)]

        cues = map_events_to_cues(events, existing_cues=existing, clear_existing=True)

        # Slot 0 (LOAD) is still occupied; slot 1 (CUE) is freed.
        assert cues[0].hotcue == 1
        assert cues[1].hotcue == 2

    def test_clear_existing_false_preserves_all_occupied_slots(self):
        """When clear_existing=False (default), all occupied slots are respected."""
        existing = [
            CuePoint(name="User Cue", type=CueType.CUE, start_ms=500.0, hotcue=0),
        ]
        events = [_event("cue", 1000.0)]

        cues = map_events_to_cues(events, existing_cues=existing, clear_existing=False)

        # Slot 0 is occupied by a TYPE="0" cue; new cue goes to slot 1.
        assert cues[0].hotcue == 1

    def test_clear_existing_with_all_eight_type_0_slots_fills_from_zero(self):
        """All 8 slots occupied by TYPE="0" cues -> all freed, fill 0..7."""
        existing = [
            CuePoint(
                name=f"Cue {i}", type=CueType.CUE, start_ms=float(i * 1000), hotcue=i
            )
            for i in range(8)
        ]
        events = [_event("cue", float(i * 1000)) for i in range(8)]

        cues = map_events_to_cues(events, existing_cues=existing, clear_existing=True)

        assert len(cues) == 8
        assert [c.hotcue for c in cues] == list(range(8))
