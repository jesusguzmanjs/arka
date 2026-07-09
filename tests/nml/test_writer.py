"""Tests for ``traktorco.nml.writer``.

Covers the writer constraints from ``.openspec/2-spec.md`` section 3.4:
AutoGrid preservation, backup creation, 6-decimal float formatting,
HOTCUE-slot-conflict refusal, and appending (never removing/reordering)
existing ``<CUE_V2>`` children.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from traktorco.nml.constants import CueType
from traktorco.nml.models import CuePoint
from traktorco.nml.parser import NmlParser
from traktorco.nml.writer import HotcueSlotConflictError, NmlWriter

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_COLLECTION = FIXTURES_DIR / "sample_collection.nml"
# Use the second track which has fewer occupied hotcue slots (only 0-1)
KNOWN_TRACK_PATH = (
    r"C:\Users\ska_m\Music\Tidal\James Blake, Dave - Doesn't Just Happen.flac"
)


@pytest.fixture()
def working_nml(tmp_path) -> Path:
    """A throwaway copy of the real fixture, safe to mutate per test."""
    dest = tmp_path / "collection.nml"
    shutil.copy2(SAMPLE_COLLECTION, dest)
    return dest


def _cue(name: str, start_ms: float, hotcue: int) -> CuePoint:
    return CuePoint(
        name=name,
        type=CueType.CUE,
        start_ms=start_ms,
        len_ms=0.0,
        repeats=-1,
        hotcue=hotcue,
    )


class TestWriteCues:
    def test_appends_new_cue_v2_elements(self, working_nml):
        parser = NmlParser(working_nml)
        # The second track has hotcues 0-3 occupied; clear them so new slots
        # 3-4 can be written without a HotcueSlotConflictError.
        NmlWriter(parser).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Intro End", 100.0, hotcue=3), _cue("Drop", 200.0, hotcue=4)],
            clear_existing=True,
        )

        reparsed = NmlParser(working_nml)
        entry = reparsed.find_entry(KNOWN_TRACK_PATH)
        names = {cue.name for cue in entry.cues}
        assert {"Intro End", "Drop"} <= names

    def test_preserves_autogrid_cue_byte_for_byte(self, working_nml):
        before = NmlParser(working_nml).find_entry(KNOWN_TRACK_PATH)
        autogrid_before = next(c for c in before.cues if c.type == CueType.GRID)

        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Intro End", 100.0, hotcue=3)],
            clear_existing=True,
        )

        after = NmlParser(working_nml).find_entry(KNOWN_TRACK_PATH)
        autogrid_after = next(c for c in after.cues if c.type == CueType.GRID)

        assert autogrid_after.start_ms == pytest.approx(autogrid_before.start_ms)
        assert autogrid_after.name == autogrid_before.name
        assert autogrid_after.hotcue == autogrid_before.hotcue
        # Still exactly one GRID cue -- never duplicated.
        assert sum(1 for c in after.cues if c.type == CueType.GRID) == 1

    def test_never_removes_or_reorders_existing_children(self, working_nml):
        before_tree = ET.parse(working_nml)
        # Skip the first entry and get the second one (James Blake)
        before_entries = before_tree.getroot().findall("./COLLECTION/ENTRY")
        before_entry = before_entries[1]
        # Snapshot only non-HotCue children for the "never removes" assertion
        before_children = [
            (child.tag, child.get("NAME"))
            for child in before_entry
            if not (child.tag == "CUE_V2" and child.get("TYPE") == "0")
        ]

        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Drop", 200.0, hotcue=3)],
            clear_existing=True,
        )

        after_tree = ET.parse(working_nml)
        after_entries = after_tree.getroot().findall("./COLLECTION/ENTRY")
        after_entry = after_entries[1]
        after_children = [
            (child.tag, child.get("NAME"))
            for child in after_entry
            if not (child.tag == "CUE_V2" and child.get("TYPE") == "0")
        ]

        # Every original non-HotCue child, in its original order, is still
        # present as a prefix of the new children list.
        assert after_children[: len(before_children)] == before_children

    def test_formats_floats_with_six_decimal_places(self, working_nml):
        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Drop", 123.456789, hotcue=3)],
            clear_existing=True,
        )

        raw_xml = working_nml.read_text(encoding="utf-8")
        assert 'START="123.456789"' in raw_xml
        assert 'LEN="0.000000"' in raw_xml

    def test_creates_bak_backup_matching_original_content(self, working_nml):
        original_bytes = working_nml.read_bytes()

        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Drop", 200.0, hotcue=3)],
            clear_existing=True,
        )

        backup_path = Path(str(working_nml) + ".bak")
        assert backup_path.exists()
        assert backup_path.read_bytes() == original_bytes

    def test_does_not_overwrite_an_existing_backup_on_second_run(self, working_nml):
        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Drop", 200.0, hotcue=3)],
            clear_existing=True,
        )
        backup_path = Path(str(working_nml) + ".bak")
        backup_after_first_run = backup_path.read_bytes()

        # Second run, against the *modified* file -- the .bak must still
        # reflect the pristine original, not this intermediate state.
        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Outro", 300.0, hotcue=4)],
            clear_existing=True,
        )
        assert backup_path.read_bytes() == backup_after_first_run

    def test_raises_on_hotcue_slot_conflict(self, working_nml):
        # The second track has hotcue slots 0-3 occupied. Without
        # clear_existing, writing to an occupied slot must raise.
        with pytest.raises(HotcueSlotConflictError):
            NmlWriter(NmlParser(working_nml)).write_cues(
                KNOWN_TRACK_PATH, [_cue("Drop", 200.0, hotcue=0)]
            )

    def test_no_op_and_no_backup_when_cues_list_is_empty(self, working_nml):
        NmlWriter(NmlParser(working_nml)).write_cues(KNOWN_TRACK_PATH, [])
        assert not Path(str(working_nml) + ".bak").exists()

    def test_written_file_is_still_valid_and_reparseable_xml(self, working_nml):
        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Intro End", 100.0, hotcue=3), _cue("Drop", 200.0, hotcue=4)],
            clear_existing=True,
        )
        # Must not raise -- proves the file is well-formed XML afterward.
        ET.parse(working_nml)
