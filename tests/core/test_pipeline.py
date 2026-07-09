"""Tests for ``traktorco.core.pipeline``.

Tests for batch processing (spec section 8.3), including error isolation
and per-track immediate writes.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from traktorco.audio.detector import DetectedEvent
from traktorco.core.pipeline import (
    BatchResult,
    BatchTrackResult,
    run_batch_pipeline,
)
from traktorco.nml.models import TempoInfo, TrackEntry
from traktorco.nml.parser import (
    AmbiguousPlaylistError,
    PlaylistNotFoundError,
    TrackNotFoundError,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_COLLECTION = FIXTURES_DIR / "sample_collection.nml"


class TestBatchResult:
    """Test BatchResult properties."""

    def test_succeeded_count_counts_non_none_detected_events(self):
        result = BatchResult(
            results=[
                BatchTrackResult(
                    entry=MagicMock(),
                    detected_events=[],  # Success (empty is still success)
                    written_cues=[],
                    error=None,
                ),
                BatchTrackResult(
                    entry=MagicMock(),
                    detected_events=None,  # Skipped
                    written_cues=[],
                    error="missing BPM",
                ),
                BatchTrackResult(
                    entry=MagicMock(),
                    detected_events=[MagicMock()],  # Success
                    written_cues=[],
                    error=None,
                ),
            ]
        )
        assert result.succeeded_count == 2

    def test_skipped_count_counts_none_detected_events(self):
        result = BatchResult(
            results=[
                BatchTrackResult(
                    entry=MagicMock(),
                    detected_events=[],
                    written_cues=[],
                    error=None,
                ),
                BatchTrackResult(
                    entry=MagicMock(),
                    detected_events=None,
                    written_cues=[],
                    error="missing or invalid BPM",
                ),
                BatchTrackResult(
                    entry=MagicMock(),
                    detected_events=None,
                    written_cues=[],
                    error="audio analysis failed",
                ),
            ]
        )
        assert result.skipped_count == 2


class TestRunBatchPipeline:
    """Test batch pipeline execution (spec section 8.3)."""

    def test_requires_exactly_one_selection_mode(self):
        """ValueError if neither or both playlist/track_title given."""
        with pytest.raises(ValueError):
            run_batch_pipeline(SAMPLE_COLLECTION)  # Neither

        with pytest.raises(ValueError):
            run_batch_pipeline(
                SAMPLE_COLLECTION,
                playlist="prueba",
                track_title="test",  # Both
            )

    def test_forbids_artist_with_playlist(self):
        """ValueError if artist filter given with playlist mode."""
        with pytest.raises(ValueError):
            run_batch_pipeline(
                SAMPLE_COLLECTION,
                playlist="prueba",
                artist="Machinedrum",  # Not allowed
            )

    def test_propagates_playlist_not_found_error(self):
        with pytest.raises(PlaylistNotFoundError):
            run_batch_pipeline(SAMPLE_COLLECTION, playlist="nonexistent")

    def test_propagates_ambiguous_playlist_error(self, tmp_path):
        nml_content = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="0"></COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="2">
<NODE TYPE="PLAYLIST" NAME="dup"><PLAYLIST ENTRIES="0" TYPE="LIST" UUID="uuid1"></PLAYLIST></NODE>
<NODE TYPE="PLAYLIST" NAME="dup"><PLAYLIST ENTRIES="0" TYPE="LIST" UUID="uuid2"></PLAYLIST></NODE>
</SUBNODES></NODE></PLAYLISTS>
</NML>"""
        nml_file = tmp_path / "dup.nml"
        nml_file.write_text(nml_content)
        with pytest.raises(AmbiguousPlaylistError):
            run_batch_pipeline(nml_file, playlist="dup")

    def test_propagates_track_not_found_for_title(self):
        with pytest.raises(TrackNotFoundError):
            run_batch_pipeline(SAMPLE_COLLECTION, track_title="Nonexistent")

    def test_processes_playlist_by_name(self):
        """Successful batch processing of a playlist."""
        with patch("traktorco.core.pipeline.detect_events") as mock_detect:
            mock_detect.return_value = [
                DetectedEvent(
                    label="cue",
                    time_ms=100.0,
                    beat_index=0,
                    is_major_phrase=False,
                    confidence=0.9,
                )
            ]

            result = run_batch_pipeline(SAMPLE_COLLECTION, playlist="prueba")

            assert isinstance(result, BatchResult)
            assert len(result.results) == 2
            # Both tracks should be attempted
            assert result.succeeded_count == 2
            assert result.skipped_count == 0

    def test_skips_tracks_with_missing_bpm(self):
        """Spec section 8.3, step 1: skip if BPM <= 0."""
        # The sample fixture has valid BPMs, so we need to patch the parser
        # to inject a zero-BPM entry
        with patch(
            "traktorco.core.pipeline.NmlParser.find_entries_by_playlist"
        ) as mock_find:
            # Create a mock track with invalid BPM
            invalid_entry = TrackEntry(
                title="Zero BPM Track",
                artist="Test",
                location_path="/path/to/track.flac",
                tempo=TempoInfo(bpm=0.0),
                cues=[],
                grid_anchor_ms=0.0,
                duration_ms=100_000.0,
            )
            mock_element = MagicMock()
            mock_find.return_value = [
                MagicMock(entry=invalid_entry, element=mock_element)
            ]

            result = run_batch_pipeline(SAMPLE_COLLECTION, playlist="test")

            assert len(result.results) == 1
            assert result.skipped_count == 1
            assert result.succeeded_count == 0
            assert result.results[0].error == "missing or invalid BPM"
            assert result.results[0].detected_events is None

    def test_skips_tracks_that_fail_audio_analysis(self):
        """Spec section 8.3, step 2: catch all exceptions during detection."""
        with patch(
            "traktorco.core.pipeline.NmlParser.find_entries_by_playlist"
        ) as mock_find:
            track_entry = TrackEntry(
                title="Bad Audio",
                artist="Test",
                location_path="/nonexistent/path.flac",
                tempo=TempoInfo(bpm=120.0),
                cues=[],
                grid_anchor_ms=0.0,
                duration_ms=100_000.0,
            )
            mock_element = MagicMock()
            mock_find.return_value = [
                MagicMock(entry=track_entry, element=mock_element)
            ]

            with patch("traktorco.core.pipeline.detect_events") as mock_detect:
                # Simulate decode failure
                mock_detect.side_effect = FileNotFoundError("Audio file not found")

                result = run_batch_pipeline(SAMPLE_COLLECTION, playlist="test")

                assert len(result.results) == 1
                assert result.skipped_count == 1
                error = result.results[0].error
                assert error is not None
                assert "not found" in error.lower()
                assert result.results[0].detected_events is None

    def test_continues_batch_on_single_track_error(self):
        """Spec section 8.3, step 5: one track's error must not stop batch."""
        with patch(
            "traktorco.core.pipeline.NmlParser.find_entries_by_playlist"
        ) as mock_find:
            # Create two tracks: first fails audio analysis, second succeeds
            failing_entry = TrackEntry(
                title="Failing Track",
                artist="Test",
                location_path="/bad.flac",
                tempo=TempoInfo(bpm=120.0),
                cues=[],
                grid_anchor_ms=0.0,
                duration_ms=100_000.0,
            )
            passing_entry = TrackEntry(
                title="Passing Track",
                artist="Test",
                location_path="/good.flac",
                tempo=TempoInfo(bpm=120.0),
                cues=[],
                grid_anchor_ms=0.0,
                duration_ms=100_000.0,
            )
            mock_failing_el = MagicMock()
            mock_passing_el = MagicMock()
            mock_find.return_value = [
                MagicMock(entry=failing_entry, element=mock_failing_el),
                MagicMock(entry=passing_entry, element=mock_passing_el),
            ]

            with patch("traktorco.core.pipeline.detect_events") as mock_detect:
                # First call fails, second succeeds
                mock_detect.side_effect = [
                    Exception("decode failed"),
                    [
                        DetectedEvent(
                            label="cue",
                            time_ms=50_000.0,
                            beat_index=100,
                            is_major_phrase=True,
                            confidence=0.95,
                        )
                    ],
                ]

                with patch("traktorco.core.pipeline.map_events_to_cues") as mock_map:
                    mock_map.return_value = []  # No cues to write

                    result = run_batch_pipeline(SAMPLE_COLLECTION, playlist="test")

            # Both entries should be in results
            assert len(result.results) == 2
            assert result.skipped_count == 1
            assert result.succeeded_count == 1

    def test_batch_title_selection_with_artist_filter(self):
        """Spec section 8.2: --track-title can be narrowed by --artist."""
        with patch("traktorco.core.pipeline.detect_events") as mock_detect:
            mock_detect.return_value = []

            result = run_batch_pipeline(
                SAMPLE_COLLECTION,
                track_title="NO 1 KNEW",
                artist="Machinedrum",
            )

            assert isinstance(result, BatchResult)
            assert len(result.results) == 1
            assert result.results[0].entry.artist == "Machinedrum"
            assert result.results[0].entry.title == "NO 1 KNEW"
