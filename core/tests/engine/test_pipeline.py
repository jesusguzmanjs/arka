"""Tests for ``cuegrid.engine.pipeline``.

Tests for batch processing (spec section 8.3), including error isolation
and one in-memory NML commit per batch.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cuegrid.audio.detector import DetectedEvent
from cuegrid.engine import (
    BatchResult,
    BatchTrackResult,
    PipelineResult,
    run_batch_pipeline,
    run_pipeline,
    serialize_gui_payload,
)
from cuegrid.nml.constants import CueType
from cuegrid.nml.models import CuePoint, TempoInfo, TrackEntry
from cuegrid.nml.parser import (
    AmbiguousPlaylistError,
    PlaylistNotFoundError,
    TrackNotFoundError,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_COLLECTION = FIXTURES_DIR / "sample_collection.nml"


class TestGuiPayload:
    def test_serializes_numpy_scalars_as_native_json_values(self, tmp_path):
        np = pytest.importorskip("numpy")
        entry = TrackEntry(
            title="GUI Track",
            artist="Test Artist",
            location_path=str(tmp_path / "track.wav"),
            tempo=TempoInfo(bpm=np.float32(120.0)),
            cues=[],
            grid_anchor_ms=np.float32(250.0),
            duration_ms=np.float32(60_000.0),
        )
        result = PipelineResult(
            entry=entry,
            detected_events=[],
            written_cues=[
                CuePoint(
                    name="Cue",
                    type=CueType.CUE,
                    start_ms=np.float32(250.0),
                    hotcue=np.int64(2),
                ),
                CuePoint(
                    name="Off Grid",
                    type=CueType.CUE,
                    start_ms=np.float32(300.0),
                    hotcue=np.int64(3),
                ),
            ],
        )

        payload = json.loads(serialize_gui_payload(result, tmp_path / "track.wav"))

        assert payload["bpm"] == 120.0
        assert payload["grid_anchor_ms"] == 250.0
        assert payload["duration_ms"] == 60_000.0
        assert payload["is_flex_grid"] is False
        assert payload["cues"] == [
            {"id": 2, "position_ms": 250.0, "is_valid": True},
            {"id": 3, "position_ms": 300.0, "is_valid": False},
        ]


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

    def test_processes_explicit_paths_and_skips_unresolved_ones(self, caplog):
        entry = TrackEntry(
            title="Resolved",
            artist="Test",
            location_path="/music/resolved.flac",
            tempo=TempoInfo(bpm=120.0),
            cues=[],
            grid_anchor_ms=0.0,
            duration_ms=100_000.0,
        )
        with patch(
            "cuegrid.engine.pipeline.NmlParser.find_entry",
            side_effect=[TrackNotFoundError("missing"), entry],
        ) as mock_find_entry, patch(
            "cuegrid.engine.pipeline.NmlParser.find_entry_element",
            return_value=MagicMock(),
        ), patch("cuegrid.engine.pipeline.detect_events", return_value=[]):
            result = run_batch_pipeline(
                SAMPLE_COLLECTION,
                track_paths=["/music/missing.flac", "/music/resolved.flac"],
            )

        assert mock_find_entry.call_count == 2
        assert len(result.results) == 1
        assert result.results[0].entry is entry
        assert "Skipping unresolved track path" in caplog.text

    def test_processes_playlist_by_name(self):
        """Successful batch processing of a playlist."""
        with patch("cuegrid.engine.pipeline.detect_events") as mock_detect:
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

    def test_persists_all_batch_cues_with_one_backup_and_one_write(self):
        """AutoCue batches mutate every entry before one final NML commit."""
        first_entry = TrackEntry(
            title="First",
            artist="Test",
            location_path="/music/first.flac",
            tempo=TempoInfo(bpm=120.0),
            cues=[],
            grid_anchor_ms=0.0,
            duration_ms=100_000.0,
        )
        second_entry = TrackEntry(
            title="Second",
            artist="Test",
            location_path="/music/second.flac",
            tempo=TempoInfo(bpm=120.0),
            cues=[],
            grid_anchor_ms=0.0,
            duration_ms=100_000.0,
        )
        first_element = MagicMock()
        second_element = MagicMock()
        cue = CuePoint(name="Cue", type=CueType.CUE, start_ms=10_000.0, hotcue=0)

        with patch("cuegrid.engine.pipeline.NmlParser.find_entries_by_playlist") as mock_find, patch(
            "cuegrid.engine.pipeline.detect_events", return_value=[]
        ), patch("cuegrid.engine.pipeline.map_events_to_cues", return_value=[cue]), patch(
            "cuegrid.engine.pipeline.NmlWriter._backup_if_needed"
        ) as mock_backup, patch(
            "cuegrid.engine.pipeline.NmlWriter.write_cues_to_element"
        ) as mock_mutate, patch(
            "cuegrid.engine.pipeline.NmlWriter._write_atomic"
        ) as mock_write:
            mock_find.return_value = [
                MagicMock(entry=first_entry, element=first_element),
                MagicMock(entry=second_entry, element=second_element),
            ]

            result = run_batch_pipeline(SAMPLE_COLLECTION, playlist="test")

        assert result.succeeded_count == 2
        assert mock_backup.call_count == 1
        assert mock_mutate.call_count == 2
        assert mock_write.call_count == 1
        assert mock_mutate.call_args_list[0].args[0] is first_element
        assert mock_mutate.call_args_list[1].args[0] is second_element

    def test_skips_tracks_with_missing_bpm(self):
        """Spec section 8.3, step 1: skip if BPM <= 0."""
        # The sample fixture has valid BPMs, so we need to patch the parser
        # to inject a zero-BPM entry
        with patch(
            "cuegrid.engine.pipeline.NmlParser.find_entries_by_playlist"
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

    def test_skips_flex_grid_before_audio_analysis(self):
        flex_entry = TrackEntry(
            title="Flex",
            artist="Test",
            location_path="/path/to/flex.flac",
            tempo=TempoInfo(bpm=120.0),
            is_flex_grid=True,
        )
        with patch("cuegrid.engine.pipeline.NmlParser.find_entries_by_playlist") as mock_find:
            mock_find.return_value = [MagicMock(entry=flex_entry, element=MagicMock())]
            with patch("cuegrid.engine.pipeline.detect_events") as mock_detect:
                result = run_batch_pipeline(SAMPLE_COLLECTION, playlist="test")

        assert result.skipped_count == 1
        assert result.results[0].error == "flex_grid"
        mock_detect.assert_not_called()

    def test_single_track_flex_grid_skips_before_audio_analysis(self):
        flex_entry = TrackEntry(
            title="Flex",
            artist="Test",
            location_path="/path/to/flex.flac",
            tempo=TempoInfo(bpm=120.0),
            is_flex_grid=True,
        )
        with patch("cuegrid.engine.pipeline.NmlParser.find_entry", return_value=flex_entry):
            with patch("cuegrid.engine.pipeline.detect_events") as mock_detect:
                result = run_pipeline(SAMPLE_COLLECTION, "/path/to/flex.flac")

        assert result.skipped_reason == "flex_grid"
        assert result.written_cues == []
        mock_detect.assert_not_called()

    def test_skips_tracks_that_fail_audio_analysis(self):
        """Spec section 8.3, step 2: catch all exceptions during detection."""
        with patch(
            "cuegrid.engine.pipeline.NmlParser.find_entries_by_playlist"
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

            with patch("cuegrid.engine.pipeline.detect_events") as mock_detect:
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
            "cuegrid.engine.pipeline.NmlParser.find_entries_by_playlist"
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

            with patch("cuegrid.engine.pipeline.detect_events") as mock_detect:
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

                with patch("cuegrid.engine.pipeline.map_events_to_cues") as mock_map:
                    mock_map.return_value = []  # No cues to write

                    result = run_batch_pipeline(SAMPLE_COLLECTION, playlist="test")

            # Both entries should be in results
            assert len(result.results) == 2
            assert result.skipped_count == 1
            assert result.succeeded_count == 1

    def test_batch_title_selection_with_artist_filter(self):
        """Spec section 8.2: --track-title can be narrowed by --artist."""
        with patch("cuegrid.engine.pipeline.detect_events") as mock_detect:
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
