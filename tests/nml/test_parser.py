"""Tests for ``traktorco.nml.parser``.

Quick verification that ``NmlParser`` can locate a track in
``tests/fixtures/sample_collection.nml`` by its audio file path and
correctly extract its BPM and grid anchor (spec sections 2.1, 2.3, 7).
"""

import logging
from pathlib import Path

import pytest

from traktorco.nml.constants import CueType
from traktorco.nml.parser import (
    AmbiguousPlaylistError,
    AmbiguousTrackError,
    BatchTrackRef,
    NmlParser,
    PlaylistNotFoundError,
    TrackNotFoundError,
    nml_location_to_path,
    primary_key_to_normalized_path,
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
        # The first entry has no PLAYTIME_FLOAT (Traktor Pro 4 / NML VERSION="20"),
        # only integer-seconds PLAYTIME="245" -- verify the fallback works.
        parser = NmlParser(SAMPLE_COLLECTION)
        entry = parser.find_entry(KNOWN_TRACK_PATH)
        assert entry.duration_ms == pytest.approx(245_000.0)

    def test_extracts_existing_cues(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        entry = parser.find_entry(KNOWN_TRACK_PATH)

        # The fixture includes multiple CUE_V2 elements (HotCues and AutoGrid)
        assert len(entry.cues) > 0
        # Find the grid cue (TYPE=4)
        grid_cue = next((c for c in entry.cues if c.type == CueType.GRID), None)
        assert grid_cue is not None
        assert grid_cue.name == "AutoGrid"
        assert grid_cue.start_ms == pytest.approx(140.010788)

    def test_extracts_loudness_metadata(self):
        """v1.9: <LOUDNESS> PEAK_DB and PERCEIVED_DB are parsed from the NML."""
        parser = NmlParser(SAMPLE_COLLECTION)
        entry = parser.find_entry(KNOWN_TRACK_PATH)

        assert entry.peak_db == pytest.approx(0.050400)
        assert entry.perceived_db == pytest.approx(-0.305168)

    def test_loudness_is_none_when_element_missing(self, tmp_path):
        """v1.9: entries without <LOUDNESS> get None for both fields."""
        nml_content = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4" />
<COLLECTION ENTRIES="1"><ENTRY TITLE="No Loudness" ARTIST="Test"><LOCATION DIR="/:Music/:" FILE="track.flac" VOLUME="C:" VOLUMEID="x" />
<INFO PLAYTIME="100" />
<TEMPO BPM="128.0" BPM_QUALITY="100.0" />
<CUE_V2 NAME="AutoGrid" DISPL_ORDER="0" TYPE="4" START="10.0" LEN="0.0" REPEATS="-1" HOTCUE="-1" />
</ENTRY></COLLECTION>
</NML>"""
        nml_file = tmp_path / "no_loudness.nml"
        nml_file.write_text(nml_content)
        parser = NmlParser(nml_file)
        entry = parser.find_entry(r"C:\Music\track.flac")
        assert entry.peak_db is None
        assert entry.perceived_db is None

    def test_case_insensitive_and_slash_insensitive_matching(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        # Different casing and forward slashes should still match, since
        # comparison is casefold()'d and separator-normalized.
        alt_path = r"c:/USERS/ska_m/MUSIC/Tidal/machinedrum - no 1 knew.flac"
        entry = parser.find_entry(alt_path)
        assert entry.title == "NO 1 KNEW"
        assert entry.artist == "Machinedrum"

    def test_raises_track_not_found_for_unknown_path(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        with pytest.raises(TrackNotFoundError):
            parser.find_entry(r"C:\Users\ska_m\Music\Tidal\Nonexistent Track.flac")


def _entry_xml(title: str, artist: str) -> str:
    """Build a minimal <ENTRY> sharing the same LOCATION, for disambiguation tests."""
    return f"""<ENTRY TITLE="{title}" ARTIST="{artist}"><LOCATION DIR="/:Users/:dj/:Music/:" FILE="track.mp3" VOLUME="C:" VOLUMEID="x"></LOCATION>
<INFO PLAYTIME="200"></INFO>
<TEMPO BPM="128.000000" BPM_QUALITY="100.000000"></TEMPO>
<LOUDNESS PEAK_DB="0.0" PERCEIVED_DB="-1.0" ANALYZED_DB="-1.0" />
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
        assert entry.artist == "Machinedrum"

    def test_find_entry_element_also_supports_filters(self, duplicate_nml):
        parser = NmlParser(duplicate_nml)
        element = parser.find_entry_element(DUPLICATE_TRACK_PATH, title="Track B")
        assert element.get("TITLE") == "Track B"
        assert element.get("ARTIST") == "Artist B"


class TestPrimaryKeyToNormalizedPath:
    """Spec section 8.1.1: convert PRIMARYKEY format to normalized path."""

    def test_windows_primary_key(self):
        key = "C:/:Users/:ska_m/:Music/:Tidal/:Machinedrum - NO 1 KNEW.flac"
        result = primary_key_to_normalized_path(key)
        # Should match what nml_location_to_path produces
        expected = nml_location_to_path(
            "C:", "/:Users/:ska_m/:Music/:Tidal/:", "Machinedrum - NO 1 KNEW.flac"
        )
        assert result == expected

    def test_matches_nml_location_to_path_exactly(self):
        # Ensure PRIMARYKEY and LOCATION produce identical normalized paths
        # This test uses different paths to verify the conversion is robust
        key = "C:/:Users/:dj/:Music/:Track.mp3"
        primary_result = primary_key_to_normalized_path(key)

        location_result = nml_location_to_path(
            "C:", "/:Users/:dj/:Music/:", "Track.mp3"
        )
        assert primary_result == location_result


class TestFindEntriesByPlaylist:
    """Spec section 8.1: resolve all tracks in a playlist."""

    def test_finds_entries_in_playlist_by_name(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        results = parser.find_entries_by_playlist("prueba")

        assert len(results) == 2
        assert all(isinstance(r, BatchTrackRef) for r in results)
        # Check entries (order should match playlist order)
        titles = {r.entry.title for r in results}
        assert "NO 1 KNEW" in titles
        assert "Doesn't Just Happen" in titles

    def test_returns_batch_track_refs_with_elements(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        results = parser.find_entries_by_playlist("prueba")

        for ref in results:
            assert isinstance(ref.entry, type(ref.entry))  # TrackEntry
            assert ref.element is not None
            # Element should be the live <ENTRY> from the tree
            assert ref.element.get("TITLE") == ref.entry.title

    def test_raises_playlist_not_found(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        with pytest.raises(PlaylistNotFoundError):
            parser.find_entries_by_playlist("nonexistent")

    def test_case_sensitive_playlist_matching(self):
        """Playlist names are case-sensitive (unlike --title/--artist)."""
        parser = NmlParser(SAMPLE_COLLECTION)
        with pytest.raises(PlaylistNotFoundError):
            parser.find_entries_by_playlist("Prueba")  # Wrong case

    def test_raises_ambiguous_playlist_error_on_duplicate_names(self, tmp_path):
        """Traktor permits duplicate playlist names; parser must fail clearly."""
        nml_content = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="0"></COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="2">
<NODE TYPE="PLAYLIST" NAME="dup"><PLAYLIST ENTRIES="0" TYPE="LIST" UUID="uuid1"></PLAYLIST></NODE>
<NODE TYPE="PLAYLIST" NAME="dup"><PLAYLIST ENTRIES="0" TYPE="LIST" UUID="uuid2"></PLAYLIST></NODE>
</SUBNODES></NODE></PLAYLISTS>
</NML>"""
        nml_file = tmp_path / "dup_playlist.nml"
        nml_file.write_text(nml_content)
        parser = NmlParser(nml_file)
        with pytest.raises(AmbiguousPlaylistError):
            parser.find_entries_by_playlist("dup")

    def test_skips_stale_primarykeys_with_warning(self, tmp_path, caplog):
        """Tracks in playlist but missing from collection are skipped."""
        nml_content = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="1"><ENTRY TITLE="Found" ARTIST="Artist"><LOCATION DIR="/:Music/:" FILE="found.flac" VOLUME="C:" VOLUMEID="x"></LOCATION>
<INFO PLAYTIME="100"></INFO>
<TEMPO BPM="128.000000" BPM_QUALITY="100.000000"></TEMPO>
<LOUDNESS PEAK_DB="0.0" PERCEIVED_DB="-1.0" ANALYZED_DB="-1.0" />
<CUE_V2 NAME="AutoGrid" DISPL_ORDER="0" TYPE="4" START="10.0" LEN="0.000000" REPEATS="-1" HOTCUE="-1"></CUE_V2>
</ENTRY></COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="1">
<NODE TYPE="PLAYLIST" NAME="test"><PLAYLIST ENTRIES="2" TYPE="LIST" UUID="uuid1">
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Music/:found.flac"></PRIMARYKEY></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Music/:missing.flac"></PRIMARYKEY></ENTRY>
</PLAYLIST></NODE>
</SUBNODES></NODE></PLAYLISTS>
</NML>"""
        nml_file = tmp_path / "stale_refs.nml"
        nml_file.write_text(nml_content)
        parser = NmlParser(nml_file)
        with caplog.at_level(logging.WARNING):
            results = parser.find_entries_by_playlist("test")

        # One track resolved, one skipped
        assert len(results) == 1
        assert results[0].entry.title == "Found"
        # Should have logged the skip
        assert "missing.flac" in caplog.text or "stale" in caplog.text.lower()


class TestFindEntriesByTitle:
    """Spec section 8.2: resolve all tracks matching a title."""

    def test_finds_entries_by_exact_case_insensitive_title(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        # Should match the entry with this title (case-insensitive)
        results = parser.find_entries_by_title("no 1 knew")

        assert len(results) >= 1
        # Find the matching entry
        match = next((r for r in results if r.entry.title == "NO 1 KNEW"), None)
        assert match is not None
        assert match.entry.artist == "Machinedrum"

    def test_returns_batch_track_refs(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        results = parser.find_entries_by_title("NO 1 KNEW")

        assert all(isinstance(r, BatchTrackRef) for r in results)
        for ref in results:
            assert ref.element is not None

    def test_case_insensitive_matching(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        results_upper = parser.find_entries_by_title("NO 1 KNEW")
        results_lower = parser.find_entries_by_title("no 1 knew")

        assert len(results_upper) == len(results_lower)
        # Both should return the same entry
        assert results_upper[0].entry.artist == results_lower[0].entry.artist

    def test_artist_filter_narrows_title_search(self):
        # Both entries have unique titles, so this won't show in fixtures,
        # but test the mechanism works
        parser = NmlParser(SAMPLE_COLLECTION)
        results = parser.find_entries_by_title("NO 1 KNEW", artist="Machinedrum")
        assert len(results) == 1
        assert results[0].entry.artist == "Machinedrum"

    def test_artist_filter_case_insensitive(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        results = parser.find_entries_by_title("NO 1 KNEW", artist="machinedrum")
        assert len(results) == 1

    def test_raises_track_not_found_on_empty_results(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        with pytest.raises(TrackNotFoundError):
            parser.find_entries_by_title("Nonexistent Title")

    def test_artist_filter_that_matches_nothing_raises(self):
        parser = NmlParser(SAMPLE_COLLECTION)
        with pytest.raises(TrackNotFoundError):
            parser.find_entries_by_title("NO 1 KNEW", artist="Wrong Artist")
