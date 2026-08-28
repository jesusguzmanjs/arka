"""Tests for ``cuegrid.nml.writer``.

Covers the writer constraints from ``.openspec/2-spec.md`` section 3.4:
AutoGrid preservation, backup creation, 6-decimal float formatting,
HOTCUE-slot-conflict refusal, and appending (never removing/reordering)
existing ``<CUE_V2>`` children.
"""

from __future__ import annotations

import pytest
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

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


class TestWriteRemixSet:
    def test_increments_existing_sets_entries(self, working_nml):
        writer = NmlWriter(NmlParser(working_nml))
        writer.write_remix_set({"title": "First", "pads": []})
        NmlWriter(NmlParser(working_nml)).write_remix_set({"title": "Second", "pads": []})

        sets = ET.parse(working_nml).getroot().find("SETS")
        assert sets is not None
        assert sets.get("ENTRIES") == "2"
        assert [set_el.get("TITLE") for set_el in sets.findall("SET")] == ["First", "Second"]

    def test_copies_audio_and_writes_native_root_set(self, working_nml, tmp_path, monkeypatch):
        source_path = tmp_path / "working" / "kick.wav"
        source_path.parent.mkdir()
        source_path.write_bytes(b"sample audio")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        payload = {
            "title": "My/Remix Set",
            "bpm": 126.5,
            "quantize_state": 0,
            "quantize_value": 8,
            "columns": [
                {"keylock": 0, "punchmode": 1, "active_cell_index": 4},
                {"keylock": 1, "punchmode": 0},
            ],
            "pads": [
                {
                    "id": "A1",
                    "path": "\\\\?\\" + str(source_path),
                    "name": "Kick",
                    "type": 2,
                    "mode": 3,
                    "sync": 0,
                    "reverse": 1,
                    "transpose": -2.5,
                    "gain": 0.75,
                    "color_id": 7,
                    "key": "8D",
                    "bpm": 126.5,
                    "start_ms": 12.5,
                    "end_ms": 999.25,
                    "duration_ms": 2_500.5,
                },
                {"id": "A5", "path": str(source_path), "name": "Later row"},
                {"id": "B1", "name": "No source"},
            ],
        }

        parser = NmlParser(working_nml)
        parser.tree.find("./COLLECTION/ENTRY/LOCATION").set("VOLUMEID", "fixture-volume-id")
        NmlWriter(parser).write_remix_set(payload)

        destination = (
            tmp_path / "home" / "Music" / "Traktor" / "Samples" / "Arka"
            / "My_Remix Set" / "A1_kick.wav"
        )
        assert destination.read_bytes() == b"sample audio"

        root = ET.parse(working_nml).getroot()
        collection = root.find("COLLECTION")
        assert collection.get("ENTRIES") == "4"
        sets = root.find("SETS")
        assert sets is not None
        assert sets.get("ENTRIES") == "1"
        remix_set = sets.find("SET")
        assert remix_set is not None
        assert remix_set.attrib == {
            "TITLE": "My/Remix Set",
            "QUANT_VAlUE": "8",
            "QUANT_STATE": "0",
        }
        virtual_location = remix_set.find("LOCATION")
        assert virtual_location is not None
        virtual_filename = virtual_location.get("FILE")
        assert virtual_filename is not None
        assert re.fullmatch(r"\d{4}y\d{2}m\d{2}d_\d{2}h\d{2}m\d{2}s000000\.set", virtual_filename)
        assert [child.tag for child in remix_set] == [
            "LOCATION",
            "MODIFICATION_INFO",
            "INFO",
            "TEMPO",
            "SLOT",
            "SLOT",
            "SLOT",
            "SLOT",
        ]
        expected_virtual_path = working_nml.parent / virtual_filename
        assert virtual_location.attrib == {
            "DIR": NmlWriter._path_to_nml_location(str(expected_virtual_path))[1],
            "FILE": virtual_filename,
            "VOLUME": NmlWriter._path_to_nml_location(str(expected_virtual_path))[0],
            "VOLUMEID": "fixture-volume-id",
        }
        assert not expected_virtual_path.exists()
        assert remix_set.find("MODIFICATION_INFO").attrib == {"AUTHOR_TYPE": "importer"}
        assert remix_set.find("INFO").attrib == {
            "IMPORT_DATE": f"{date.today().year}/{date.today().month}/{date.today().day}",
        }
        assert remix_set.find("TEMPO").attrib == {"BPM": "126.500000"}

        assert all(
            entry.find("LOCATION").get("FILE") != virtual_filename
            for entry in collection.findall("ENTRY")
        )
        first_sample_entry, second_sample_entry = collection.findall("ENTRY")[-2:]
        assert [entry.get("TITLE") for entry in (first_sample_entry, second_sample_entry)] == [
            "Kick",
            "Later row",
        ]
        assert all(
            entry.attrib["MODIFIED_DATE"]
            == f"{date.today().year}/{date.today().month}/{date.today().day}"
            and 0 <= int(entry.attrib["MODIFIED_TIME"]) < 86_400
            and entry.attrib["LOCK"] == "1"
            and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
                entry.attrib["LOCK_MODIFICATION_TIME"],
            )
            for entry in (first_sample_entry, second_sample_entry)
        )
        assert [entry.find("LOCATION").get("VOLUMEID") for entry in (first_sample_entry, second_sample_entry)] == [
            "fixture-volume-id",
            "fixture-volume-id",
        ]
        assert [child.tag for child in first_sample_entry] == [
            "LOCATION",
            "MODIFICATION_INFO",
            "INFO",
            "TEMPO",
            "MUSICAL_KEY",
            "CUE_V2",
        ]
        assert first_sample_entry.find("MODIFICATION_INFO").attrib == {"AUTHOR_TYPE": "importer"}
        assert first_sample_entry.find("INFO").attrib == {
            "IMPORT_DATE": f"{date.today().year}/{date.today().month}/{date.today().day}",
            "FLAGS": "28",
            "COMMENT": "Arka: My/Remix Set",
            "PLAYTIME": "2",
            "PLAYTIME_FLOAT": "2.500500",
            "FILESIZE": str(destination.stat().st_size),
        }
        assert first_sample_entry.find("TEMPO").attrib == {
            "BPM": "126.500000",
            "BPM_QUALITY": "100.000000",
        }
        assert first_sample_entry.find("MUSICAL_KEY").attrib == {"VALUE": "1"}
        assert first_sample_entry.find("CUE_V2").attrib == {
            "NAME": "AutoGrid",
            "DISPL_ORDER": "0",
            "TYPE": "4",
            "START": "0.000000",
            "LEN": "0.000000",
            "REPEATS": "-1",
            "HOTCUE": "-1",
        }
        assert first_sample_entry.find("CUE_V2/GRID").attrib == {"BPM": "126.500000"}
        assert second_sample_entry.find("INFO").attrib == {
            "IMPORT_DATE": f"{date.today().year}/{date.today().month}/{date.today().day}",
            "FLAGS": "28",
            "COMMENT": "Arka: My/Remix Set",
            "FILESIZE": str(destination.stat().st_size),
        }
        assert second_sample_entry.find("TEMPO").attrib == {
            "BPM": "120.000000",
            "BPM_QUALITY": "100.000000",
        }
        assert second_sample_entry.find("MUSICAL_KEY") is None
        assert second_sample_entry.find("CUE_V2/GRID").attrib == {"BPM": "120.000000"}

        slots = remix_set.findall("SLOT")
        assert len(slots) == 4
        assert slots[0].attrib == {
            "KEYLOCK": "0",
            "PUNCHMODE": "1",
            "FXENABLE": "1",
            "ACTIVE_CELL_INDEX": "0",
        }
        assert slots[1].attrib == {
            "KEYLOCK": "1",
            "PUNCHMODE": "0",
            "FXENABLE": "1",
            "ACTIVE_CELL_INDEX": "-1",
        }
        assert [len(slot.findall("CELL")) for slot in slots] == [2, 0, 0, 0]

        first_cell, second_cell = slots[0].findall("CELL")
        assert first_cell.attrib == {
            "INDEX": "0",
            "CELLNAME": "Kick",
            "COLOR": "7",
            "SYNC": "0",
            "REVERSE": "1",
            "MODE": "3",
            "TYPE": "2",
            "SPEED": "1.000000",
            "TRANSPOSE": "-2.500000",
            "OFFSET": "0.000000",
            "NUDGE": "0.000000",
            "GAIN": "0.750000",
            "START_MARKER": "12.500000",
            "END_MARKER": "999.250000",
            "BPM": "126.500000",
            "DIR": NmlWriter._path_to_nml_location(str(destination))[1],
            "FILE": "A1_kick.wav",
            "VOLUME": NmlWriter._path_to_nml_location(str(destination))[0],
        }
        assert second_cell.get("INDEX") == "4"
        assert second_cell.get("FILE") == "A5_kick.wav"
        assert first_cell.find("LOCATION") is None
        assert len(_backup_files(working_nml)) == 1

    def test_writes_file_playtime_for_untrimmed_pad(self, working_nml, tmp_path, monkeypatch):
        source_path = tmp_path / "full-length-loop.wav"
        source_path.write_bytes(b"full length loop")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

        NmlWriter(NmlParser(working_nml)).write_remix_set(
            {
                "title": "Full length loop",
                "pads": [{
                    "id": "A1",
                    "path": str(source_path),
                    "start_ms": 0,
                    "end_ms": 0,
                    "duration_ms": 12_345.678,
                }],
            }
        )

        root = ET.parse(working_nml).getroot()
        sample_info = root.find("./COLLECTION/ENTRY[@TITLE='A1']/INFO")
        cell = root.find("./SETS/SET/SLOT/CELL")
        assert sample_info is not None
        assert sample_info.get("PLAYTIME") == "12"
        assert sample_info.get("PLAYTIME_FLOAT") == "12.345678"
        assert cell is not None
        assert cell.get("END_MARKER") == "0.000000"

    def test_reuses_existing_collection_audio_without_copying_or_replacing_entry(
        self, working_nml, tmp_path, monkeypatch
    ):
        source_path = tmp_path / "existing.wav"
        source_path.write_bytes(b"existing sample")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

        parser = NmlParser(working_nml)
        collection = parser.tree.find("COLLECTION")
        assert collection is not None
        original_entry = ET.SubElement(
            collection, "ENTRY", TITLE="Original sample", ARTIST="Artist"
        )
        volume, directory, filename = NmlWriter._path_to_nml_location(str(source_path))
        location = ET.SubElement(original_entry, "LOCATION")
        location.set("VOLUME", volume)
        location.set("DIR", directory)
        location.set("FILE", filename)
        ET.SubElement(original_entry, "STRIPE", VERSION="preserve-me")
        original_entry_xml = ET.tostring(original_entry)

        NmlWriter(parser).write_remix_set(
            {
                "title": "Reuse existing",
                "pads": [{"id": "A1", "path": str(source_path), "name": "Renamed pad"}],
            }
        )

        root = ET.parse(working_nml).getroot()
        collection = root.find("COLLECTION")
        assert collection is not None
        reloaded_original = next(
            entry
            for entry in collection.findall("ENTRY")
            if entry.get("TITLE") == "Original sample"
        )
        assert ET.tostring(reloaded_original) == original_entry_xml
        assert len(collection.findall("ENTRY")) == 3
        assert not (tmp_path / "home" / "Music" / "Traktor" / "Samples" / "Arka").exists()

        cell = root.find("./SETS/SET/SLOT/CELL")
        assert cell is not None
        assert (cell.get("VOLUME"), cell.get("DIR"), cell.get("FILE")) == (
            volume,
            directory,
            filename,
        )

    def test_replaces_same_title_set_in_place_and_keeps_collection_unique(
        self, working_nml, tmp_path, monkeypatch
    ):
        source_path = tmp_path / "loop.wav"
        source_path.write_bytes(b"loop")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        payload = {"title": "Overwrite", "quantize_value": 4, "pads": [{"id": "A1", "path": str(source_path)}]}

        NmlWriter(NmlParser(working_nml)).write_remix_set(payload)
        NmlWriter(NmlParser(working_nml)).write_remix_set({"title": "Other", "pads": []})
        payload["quantize_value"] = 16
        NmlWriter(NmlParser(working_nml)).write_remix_set(payload)

        root = ET.parse(working_nml).getroot()
        sets = root.find("SETS")
        collection = root.find("COLLECTION")
        assert sets is not None
        assert collection is not None
        assert [set_el.get("TITLE") for set_el in sets.findall("SET")] == ["Overwrite", "Other"]
        assert sets.get("ENTRIES") == "2"
        assert sets.find("SET").get("QUANT_VAlUE") == "16"
        assert len(collection.findall("ENTRY")) == 3


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

    def test_skips_grid_update_for_flex_grid_and_saves_hotcues(self, working_nml):
        tree = ET.parse(working_nml)
        entry = tree.getroot().findall("./COLLECTION/ENTRY")[1]
        ET.SubElement(entry, "CUE_V2", TYPE="4", START="999.000000")
        tree.write(working_nml, encoding="UTF-8", xml_declaration=True)

        NmlWriter(NmlParser(working_nml)).update_track_hotcues(
            KNOWN_TRACK_PATH,
            [{"hotcue": 2, "start_ms": 500.0}],
            grid_anchor_ms=500.0,
        )

        entry = ET.parse(working_nml).getroot().findall("./COLLECTION/ENTRY")[1]
        assert [cue.get("START") for cue in entry.findall("CUE_V2") if cue.get("TYPE") == "4"] == [
            "63.084743",
            "999.000000",
        ]
        assert any(
            cue.get("TYPE") == "0" and cue.get("HOTCUE") == "2" and cue.get("START") == "500.000000"
            for cue in entry.findall("CUE_V2")
        )
        assert len(_backup_files(working_nml)) == 1

    def test_batch_save_skips_missing_analysis_nodes_and_saves_metadata(self, working_nml):
        parser = NmlParser(working_nml)
        entry = parser.find_entry_element(KNOWN_TRACK_PATH)
        grid = next(cue for cue in entry.findall("CUE_V2") if cue.get("TYPE") == "4")
        entry.remove(grid)
        tempo = entry.find("TEMPO")
        assert tempo is not None
        entry.remove(tempo)

        NmlWriter(parser).write_batch_save(
            [(entry, [{"hotcue": 2, "start_ms": 500.0}], 0.0, 120.0, {"genre": "Techno"})]
        )

        saved_entry = ET.parse(working_nml).getroot().findall("./COLLECTION/ENTRY")[1]
        assert not saved_entry.findall("CUE_V2[@TYPE='4']")
        assert saved_entry.find("TEMPO") is None
        assert saved_entry.find("INFO").get("GENRE") == "Techno"
        assert any(
            cue.get("TYPE") == "0" and cue.get("HOTCUE") == "2" and cue.get("START") == "500.000000"
            for cue in saved_entry.findall("CUE_V2")
        )
        assert len(_backup_files(working_nml)) == 1

    def test_treats_parser_default_bpm_as_no_update(self, working_nml):
        NmlWriter(NmlParser(working_nml)).update_track_hotcues(
            KNOWN_TRACK_PATH,
            [],
            bpm=0.0,
        )

        entry = NmlParser(working_nml).find_entry(KNOWN_TRACK_PATH)
        assert entry.tempo.bpm == pytest.approx(60.000179)

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
