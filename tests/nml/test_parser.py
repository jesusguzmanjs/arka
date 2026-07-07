"""Tests for ``traktorco.nml.parser``.

Quick verification that ``NmlParser`` can locate a track in
``tests/fixtures/sample_collection.nml`` by its audio file path and
correctly extract its BPM and grid anchor (spec sections 2.1, 2.3, 7).
"""

from pathlib import Path

import pytest

from traktorco.nml.constants import CueType
from traktorco.nml.parser import (
    AmbiguousTrackError,
    NmlParser,
    TrackNotFoundError,
    nml_location_to_path,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_COLLECTION = FIXTURES_DIR / "sample_collection.nml"

# Matches the single <ENTRY><LOCATION> in sample_collection.nml:
#   VOLUME="C:" DIR="/:Users/:ska_m/:Music/:Tidal/:" FILE="Machinedrum - NO 1 KNEW.flac"
KNOWN_TRACK_PATH = r"C:\Users\ska_m\Music\Tidal\Machinedrum - NO 1 KNEW.flac"


class TestNmlLocationToPath:
    def test_windows_drive_volume(self):
        result = nml_location_to_path(
            volume="C:",
            dir_="/:Users/:ska_m/:Music/:Tidal/:",
            file_="Machinedrum - NO 1 KNEW.flac",
        )
        assert result == "c:/users/ska_m/music/tidal/machinedrum - no 1 knew.flac"

    def test_macos_volume_name(self):
        result = nml_location_to_path(
            volume="Macintosh HD",
            dir_="/:Users/:dj/:Music/:",
            file_="track.mp3",
        )
        assert result == "/volumes/macintosh hd/users/dj/music/track.mp3"


class TestFindEntry:
    def test_locates_track_and_extracts_bpm_and_grid_anchor(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        entry = parser.find_entry(KNOWN_TRACK_PATH)

        assert entry.title == "NO 1 KNEW"
        assert entry.artist == "Machinedrum"
        assert entry.tempo.bpm == pytest.approx(139.998703)
        assert entry.grid_anchor_ms == pytest.approx(140.010788)

    def test_extracts_duration_from_playtime_fallback(self):
        # This fixture has no PLAYTIME_FLOAT (Traktor Pro 4 / NML VERSION="20"),
        # only integer-seconds PLAYTIME="245" -- verify the fallback works.
        parser = NmlParser(SAMPLE_COLLECTION)
        entry = parser.find_entry(KNOWN_TRACK_PATH)
        assert entry.duration_ms == pytest.approx(245_000.0)

    def test_extracts_existing_cues(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        entry = parser.find_entry(KNOWN_TRACK_PATH)

        assert len(entry.cues) == 1
        grid_cue = entry.cues[0]
        assert grid_cue.type == CueType.GRID
        assert grid_cue.name == "AutoGrid"
        assert grid_cue.start_ms == pytest.approx(140.010788)

    def test_case_insensitive_and_slash_insensitive_matching(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        # Different casing and forward slashes should still match, since
        # comparison is casefold()'d and separator-normalized.
        alt_path = r"c:/USERS/ska_m/MUSIC/Tidal/machinedrum - no 1 knew.flac"
        entry = parser.find_entry(alt_path)
        assert entry.title == "NO 1 KNEW"

    def test_raises_track_not_found_for_unknown_path(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        with pytest.raises(TrackNotFoundError):
            parser.find_entry(r"C:\Users\ska_m\Music\Tidal\Nonexistent Track.flac")


def _entry_xml(title: str, artist: str) -> str:
    """Build a minimal <ENTRY> sharing the same LOCATION, for disambiguation tests."""
    return f"""<ENTRY TITLE="{title}" ARTIST="{artist}"><LOCATION DIR="/:Users/:dj/:Music/:" FILE="track.mp3" VOLUME="C:" VOLUMEID="x"></LOCATION>
<INFO PLAYTIME="200"></INFO>
<TEMPO BPM="128.000000" BPM_QUALITY="100.000000"></TEMPO>
<CUE_V2 NAME="AutoGrid" DISPL_ORDER="0" TYPE="4" START="10.0" LEN="0.000000" REPEATS="-1" HOTCUE="-1"></CUE_V2>
</ENTRY>"""


def _write_duplicate_collection(path: Path, entries: list[str]) -> Path:
    """Write a minimal collection.nml with multiple ENTRYs sharing one LOCATION.

    Traktor itself normally prevents this, but nml.parser must still
    handle it safely (spec section 7.3, step 6) -- this helper is shared
    by both the plain ambiguity test and the --title/--artist
    disambiguation tests below.
    """
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="{len(entries)}">
{"".join(entries)}
</COLLECTION>
</NML>""",
        encoding="utf-8",
    )
    return path


DUPLICATE_TRACK_PATH = r"C:\Users\dj\Music\track.mp3"


class TestAmbiguousTrackDetection:
    def test_raises_when_multiple_entries_share_a_location(self, tmp_path):
        duplicate_nml = _write_duplicate_collection(
            tmp_path / "duplicate_collection.nml",
            [_entry_xml("Track A", "Artist A"), _entry_xml("Track B", "Artist B")],
        )
        parser = NmlParser(duplicate_nml)
        with pytest.raises(AmbiguousTrackError):
            parser.find_entry(DUPLICATE_TRACK_PATH)

    def test_ambiguous_error_message_lists_candidates(self, tmp_path):
        duplicate_nml = _write_duplicate_collection(
            tmp_path / "duplicate_collection.nml",
            [_entry_xml("Track A", "Artist A"), _entry_xml("Track B", "Artist B")],
        )
        parser = NmlParser(duplicate_nml)
        with pytest.raises(AmbiguousTrackError) as exc_info:
            parser.find_entry(DUPLICATE_TRACK_PATH)
        message = str(exc_info.value)
        assert "Track A" in message
        assert "Track B" in message


class TestDisambiguationFilters:
    """Spec section 7.3, step 6: resolving AmbiguousTrackError via --title/--artist."""

    @pytest.fixture()
    def duplicate_nml(self, tmp_path) -> Path:
        return _write_duplicate_collection(
            tmp_path / "duplicate_collection.nml",
            [_entry_xml("Track A", "Artist A"), _entry_xml("Track B", "Artist B")],
        )

    def test_title_filter_resolves_ambiguity(self, duplicate_nml):
        parser = NmlParser(duplicate_nml)
        entry = parser.find_entry(DUPLICATE_TRACK_PATH, title="Track B")
        assert entry.title == "Track B"
        assert entry.artist == "Artist B"

    def test_artist_filter_resolves_ambiguity(self, duplicate_nml):
        parser = NmlParser(duplicate_nml)
        entry = parser.find_entry(DUPLICATE_TRACK_PATH, artist="Artist A")
        assert entry.title == "Track A"

    def test_title_filter_is_case_insensitive(self, duplicate_nml):
        parser = NmlParser(duplicate_nml)
        entry = parser.find_entry(DUPLICATE_TRACK_PATH, title="track b")
        assert entry.title == "Track B"

    def test_combined_title_and_artist_filters(self, duplicate_nml):
        parser = NmlParser(duplicate_nml)
        entry = parser.find_entry(
            DUPLICATE_TRACK_PATH, title="Track A", artist="Artist A"
        )
        assert entry.title == "Track A"

    def test_filters_that_match_nothing_raise_track_not_found(self, duplicate_nml):
        parser = NmlParser(duplicate_nml)
        with pytest.raises(TrackNotFoundError):
            parser.find_entry(DUPLICATE_TRACK_PATH, title="Nonexistent Title")

    def test_filters_that_still_match_multiple_entries_raise_ambiguous(self, tmp_path):
        # Two entries sharing both LOCATION and TITLE -- artist alone must
        # be supplied too, or ambiguity persists (spec 7.3, step 6: filters
        # narrow down, they don't guarantee resolution on their own).
        duplicate_nml = _write_duplicate_collection(
            tmp_path / "still_ambiguous.nml",
            [
                _entry_xml("Same Title", "Artist A"),
                _entry_xml("Same Title", "Artist B"),
            ],
        )
        parser = NmlParser(duplicate_nml)
        with pytest.raises(AmbiguousTrackError):
            parser.find_entry(DUPLICATE_TRACK_PATH, title="Same Title")

    def test_filters_are_ignored_when_location_match_is_already_unique(self):
        # A title/artist filter that doesn't even match the unique entry
        # should not spuriously narrow it away -- filters only apply once
        # LOCATION matching has produced more than one candidate (spec
        # 7.3: step 6 only triggers "if a LOCATION match raises
        # AmbiguousTrackError").
        parser = NmlParser(SAMPLE_COLLECTION)
        entry = parser.find_entry(
            KNOWN_TRACK_PATH, title="This Does Not Match Anything"
        )
        assert entry.title == "NO 1 KNEW"

    def test_find_entry_element_also_supports_filters(self, duplicate_nml):
        parser = NmlParser(duplicate_nml)
        element = parser.find_entry_element(DUPLICATE_TRACK_PATH, title="Track B")
        assert element.get("TITLE") == "Track B"
