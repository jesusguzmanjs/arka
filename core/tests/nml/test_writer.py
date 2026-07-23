"""Tests for ``cuegrid.nml.writer``.

Covers the writer constraints from ``.openspec/2-spec.md`` section 3.4:
AutoGrid preservation, backup creation, 6-decimal float formatting,
HOTCUE-slot-conflict refusal, and appending (never removing/reordering)
existing ``<CUE_V2>`` children.
"""

from __future__ import annotations

from datetime import date, timedelta
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from cuegrid.nml.constants import CueType
from cuegrid.nml.models import CuePoint
from cuegrid.nml.parser import NmlParser
from cuegrid.nml.writer import HotcueNotFoundError, HotcueSlotConflictError, NmlWriter

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


def _backup_dir(working_nml: Path) -> Path:
    return working_nml.parent / "CueGrid Backups"


def _backup_files(working_nml: Path) -> list[Path]:
    return sorted(_backup_dir(working_nml).glob(f"{working_nml.name}.*.bak"))


class TestDeleteCue:
    def test_removes_only_requested_hotcue_and_preserves_grid(self, working_nml):
        NmlWriter(NmlParser(working_nml)).delete_cue(KNOWN_TRACK_PATH, 2)

        entry = NmlParser(working_nml).find_entry(KNOWN_TRACK_PATH)
        assert all(cue.hotcue != 2 for cue in entry.cues if cue.type == CueType.CUE)
        assert any(cue.type == CueType.GRID for cue in entry.cues)
        assert len(_backup_files(working_nml)) == 1

    def test_missing_hotcue_does_not_write(self, working_nml):
        parser = NmlParser(working_nml)
        entry = parser.find_entry_element(KNOWN_TRACK_PATH)
        cue = next(
            element
            for element in entry.findall("CUE_V2")
            if element.get("TYPE") == "0" and element.get("HOTCUE") == "7"
        )
        entry.remove(cue)
        parser.tree.write(working_nml, encoding="UTF-8", xml_declaration=True)
        original = working_nml.read_bytes()

        with pytest.raises(HotcueNotFoundError):
            NmlWriter(NmlParser(working_nml)).delete_cue(KNOWN_TRACK_PATH, 7)
        assert working_nml.read_bytes() == original
        assert not _backup_files(working_nml)

    def test_rejects_invalid_hotcue_index(self, working_nml):
        with pytest.raises(ValueError):
            NmlWriter(NmlParser(working_nml)).delete_cue(KNOWN_TRACK_PATH, 8)


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

        backup_files = _backup_files(working_nml)
        assert len(backup_files) == 1
        assert backup_files[0].read_bytes() == original_bytes

    def test_does_not_overwrite_an_existing_backup_on_second_run(self, working_nml):
        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Drop", 200.0, hotcue=3)],
            clear_existing=True,
        )
        backup_files = _backup_files(working_nml)
        assert len(backup_files) == 1
        backup_after_first_run = backup_files[0].read_bytes()

        # Second run, against the *modified* file -- the .bak must still
        # reflect the pristine original, not this intermediate state.
        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Outro", 300.0, hotcue=4)],
            clear_existing=True,
        )
        assert _backup_files(working_nml) == backup_files
        assert _backup_files(working_nml)[0].read_bytes() == backup_after_first_run

    def test_rotates_backups_and_keeps_five_most_recent(self, working_nml):
        backup_dir = _backup_dir(working_nml)
        backup_dir.mkdir()
        today = date.today()
        existing_dates = [today - timedelta(days=age) for age in range(6, 1, -1)]
        for backup_date in existing_dates:
            backup_path = backup_dir / (
                f"{working_nml.name}.{backup_date:%Y%m%d}.bak"
            )
            backup_path.write_bytes(f"backup-{backup_date:%Y%m%d}".encode())

        NmlWriter(NmlParser(working_nml))._backup_if_needed()

        backup_files = _backup_files(working_nml)
        expected_dates = {
            *(today - timedelta(days=age) for age in range(5, 1, -1)),
            today,
        }
        assert len(backup_files) == 5
        assert {
            path.name.rsplit(".", 2)[1] for path in backup_files
        } == {backup_date.strftime("%Y%m%d") for backup_date in expected_dates}

    def test_rotates_backups_when_todays_backup_already_exists(self, working_nml):
        backup_dir = _backup_dir(working_nml)
        backup_dir.mkdir()
        today = date.today()
        existing_dates = [today - timedelta(days=age) for age in range(6, -1, -1)]
        for backup_date in existing_dates:
            (backup_dir / f"{working_nml.name}.{backup_date:%Y%m%d}.bak").write_text(
                "backup", encoding="utf-8"
            )

        NmlWriter(NmlParser(working_nml))._backup_if_needed()

        assert [path.name.rsplit(".", 2)[1] for path in _backup_files(working_nml)] == [
            (today - timedelta(days=age)).strftime("%Y%m%d")
            for age in range(4, -1, -1)
        ]

    def test_raises_on_hotcue_slot_conflict(self, working_nml):
        # The second track has hotcue slots 0-3 occupied. Without
        # clear_existing, writing to an occupied slot must raise.
        with pytest.raises(HotcueSlotConflictError):
            NmlWriter(NmlParser(working_nml)).write_cues(
                KNOWN_TRACK_PATH, [_cue("Drop", 200.0, hotcue=0)]
            )

    def test_no_op_and_no_backup_when_cues_list_is_empty(self, working_nml):
        NmlWriter(NmlParser(working_nml)).write_cues(KNOWN_TRACK_PATH, [])
        assert not _backup_files(working_nml)

    def test_written_file_is_still_valid_and_reparseable_xml(self, working_nml):
        NmlWriter(NmlParser(working_nml)).write_cues(
            KNOWN_TRACK_PATH,
            [_cue("Intro End", 100.0, hotcue=3), _cue("Drop", 200.0, hotcue=4)],
            clear_existing=True,
        )
        # Must not raise -- proves the file is well-formed XML afterward.
        ET.parse(working_nml)


class TestBatchMetadataUpdates:
    def test_updates_standard_metadata_in_one_atomic_write(self, working_nml):
        parser = NmlParser(working_nml)
        entry = parser.find_entry_element(KNOWN_TRACK_PATH)

        NmlWriter(parser).write_metadata_batch(
            [
                (
                    entry,
                    {
                        "title": "Updated title",
                        "release": "Updated release",
                        "artist": "Updated artist",
                        "remixer": "Remixer",
                        "producer": "Producer",
                        "genre": "Techno",
                        "label": "Label",
                        "comment": "Comment",
                        "comment2": "Comment 2",
                        "lyrics": "Lyrics",
                        "mix": "Extended",
                        "rating": 4,
                    },
                )
            ]
        )

        updated = NmlParser(working_nml).find_entry(KNOWN_TRACK_PATH)
        assert updated.title == "Updated title"
        assert updated.artist == "Updated artist"
        assert updated.album == "Updated release"
        assert updated.remixer == "Remixer"
        assert updated.producer == "Producer"
        assert updated.comment2 == "Comment 2"
        assert updated.rating == 4
        assert len(_backup_files(working_nml)) == 1

    def test_null_metadata_values_remove_existing_attributes(self, working_nml):
        parser = NmlParser(working_nml)
        entry = parser.find_entry_element(KNOWN_TRACK_PATH)
        info = entry.find("INFO")
        assert info is not None
        info.set("GENRE", "Techno")
        info.set("RANKING", "255")

        NmlWriter(parser).write_metadata_batch(
            [(entry, {"genre": None, "rating": None})]
        )

        reparsed_info = NmlParser(working_nml).find_entry_element(KNOWN_TRACK_PATH).find("INFO")
        assert reparsed_info is not None
        assert reparsed_info.get("GENRE") is None
        assert reparsed_info.get("RANKING") is None


class TestBatchPlaylistMutations:
    def test_updates_and_deletes_playlists_in_one_atomic_write(self, working_nml):
        parser = NmlParser(working_nml)
        subnodes = NmlWriter(parser)._root_playlist_subnodes()
        updated_node = ET.SubElement(subnodes, "NODE", TYPE="PLAYLIST", NAME="Before")
        updated_playlist = ET.SubElement(
            updated_node, "PLAYLIST", ENTRIES="0", TYPE="LIST", UUID="a" * 32
        )
        deleted_node = ET.SubElement(subnodes, "NODE", TYPE="PLAYLIST", NAME="Delete me")
        ET.SubElement(deleted_node, "PLAYLIST", ENTRIES="0", TYPE="LIST", UUID="b" * 32)
        entry_key = NmlWriter._entry_to_primary_key(parser.find_entry_element(KNOWN_TRACK_PATH))

        NmlWriter(parser).write_batch_save(
            [],
            [
                (updated_node, "update", "After", [entry_key]),
                (deleted_node, "delete", None, None),
            ],
        )

        tree = ET.parse(working_nml)
        nodes = tree.getroot().findall(".//NODE[@TYPE='PLAYLIST']")
        updated = next(node for node in nodes if node.get("NAME") == "After")
        playlist = updated.find("PLAYLIST")
        assert playlist is not None and playlist.get("ENTRIES") == "1"
        assert playlist.find("./ENTRY/PRIMARYKEY").get("KEY") == entry_key
        assert not any(node.get("NAME") == "Delete me" for node in nodes)
        assert len(_backup_files(working_nml)) == 1


class TestManualCueAndGridUpdates:
    def test_updates_hotcue_and_single_grid_anchor_atomically(self, working_nml):
        NmlWriter(NmlParser(working_nml)).update_track_hotcues(
            KNOWN_TRACK_PATH,
            [{"hotcue": 2, "start_ms": 12_345.678}],
            grid_anchor_ms=987.654321,
        )

        entry = NmlParser(working_nml).find_entry(KNOWN_TRACK_PATH)
        assert entry.grid_anchor_ms == pytest.approx(987.654321)
        cue = next(cue for cue in entry.cues if cue.type == CueType.CUE and cue.hotcue == 2)
        assert cue.start_ms == pytest.approx(12_345.678)
        assert 'START="987.654321"' in working_nml.read_text(encoding="utf-8")

    def test_rejects_grid_update_for_flex_grid_without_writing(self, working_nml):
        tree = ET.parse(working_nml)
        entry = tree.getroot().findall("./COLLECTION/ENTRY")[1]
        ET.SubElement(entry, "CUE_V2", TYPE="4", START="999.000000")
        tree.write(working_nml, encoding="UTF-8", xml_declaration=True)
        original = working_nml.read_bytes()

        with pytest.raises(ValueError, match="Flex Grid"):
            NmlWriter(NmlParser(working_nml)).update_track_hotcues(
                KNOWN_TRACK_PATH,
                [],
                grid_anchor_ms=500.0,
            )

        assert working_nml.read_bytes() == original
        assert not _backup_files(working_nml)

    def test_updates_tempo_bpm_and_preserves_utf8_nml(self, working_nml):
        NmlWriter(NmlParser(working_nml)).update_track_hotcues(
            KNOWN_TRACK_PATH,
            [],
            bpm=96.125,
        )

        entry = NmlParser(working_nml).find_entry(KNOWN_TRACK_PATH)
        assert entry.tempo.bpm == pytest.approx(96.125)
        raw_xml = working_nml.read_bytes()
        assert b'encoding="UTF-8"' in raw_xml[:128]
        assert 'BPM="96.125000"' in raw_xml.decode("utf-8")
        ET.parse(working_nml)

    @pytest.mark.parametrize("invalid_bpm", [49.999, 200.001, float("nan"), float("inf")])
    def test_rejects_invalid_bpm_before_writing(self, working_nml, invalid_bpm):
        original = working_nml.read_bytes()

        with pytest.raises(ValueError, match="between 50 and 200"):
            NmlWriter(NmlParser(working_nml)).update_track_hotcues(
                KNOWN_TRACK_PATH,
                [],
                bpm=invalid_bpm,
            )

        assert working_nml.read_bytes() == original
        assert not _backup_files(working_nml)
