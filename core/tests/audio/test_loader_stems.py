"""Tests for the dormant, opt-in legacy Stem extraction reference code."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cuegrid.audio.legacy_stems import DRUMS_STEM_STREAM_INDEX, extract_drum_stem


class _FakeFfmpegError(Exception):
    def __init__(self, *args: object, stderr: bytes | None = None) -> None:
        super().__init__(*args)
        self.stderr = stderr


class TestExtractDrumStem:
    def test_reports_a_clear_error_when_optional_dependency_is_absent(self):
        with patch(
            "cuegrid.audio.legacy_stems.importlib.import_module",
            side_effect=ModuleNotFoundError("No module named 'ffmpeg'"),
        ):
            with pytest.raises(RuntimeError, match="intentionally not installed"):
                extract_drum_stem("fake_sidecar.stem.mp4")

    def test_invokes_ffmpeg_with_expected_stream_map(self):
        with patch("cuegrid.audio.legacy_stems._load_ffmpeg") as load_ffmpeg:
            mock_ffmpeg = load_ffmpeg.return_value
            mock_stream = MagicMock()
            mock_ffmpeg.input.return_value = mock_stream
            mock_stream.output.return_value = mock_stream
            mock_stream.overwrite_output.return_value = mock_stream

            result_path = extract_drum_stem("fake_sidecar.stem.mp4")

            mock_ffmpeg.input.assert_called_once_with("fake_sidecar.stem.mp4")
            _, output_kwargs = mock_stream.output.call_args
            assert output_kwargs["map"] == f"0:{DRUMS_STEM_STREAM_INDEX}"
            assert output_kwargs["acodec"] == "pcm_s16le"
            mock_stream.run.assert_called_once()

            assert result_path.suffix == ".wav"
            result_path.unlink(missing_ok=True)

    def test_custom_stream_index_is_passed_through(self):
        with patch("cuegrid.audio.legacy_stems._load_ffmpeg") as load_ffmpeg:
            mock_ffmpeg = load_ffmpeg.return_value
            mock_stream = MagicMock()
            mock_ffmpeg.input.return_value = mock_stream
            mock_stream.output.return_value = mock_stream
            mock_stream.overwrite_output.return_value = mock_stream

            result_path = extract_drum_stem("fake_sidecar.stem.mp4", stream_index=2)

            _, output_kwargs = mock_stream.output.call_args
            assert output_kwargs["map"] == "0:2"
            result_path.unlink(missing_ok=True)

    def test_cleans_up_temp_file_and_reraises_on_ffmpeg_error(self):
        with patch("cuegrid.audio.legacy_stems._load_ffmpeg") as load_ffmpeg:
            mock_ffmpeg = load_ffmpeg.return_value
            mock_stream = MagicMock()
            mock_ffmpeg.input.return_value = mock_stream
            mock_stream.output.return_value = mock_stream
            mock_stream.overwrite_output.return_value = mock_stream
            mock_stream.run.side_effect = _FakeFfmpegError(
                "stream 0:1 not found", stderr=b"stream 0:1 not found"
            )

            with pytest.raises(_FakeFfmpegError):
                extract_drum_stem("fake_sidecar.stem.mp4")

    def test_cleans_up_temp_file_on_unexpected_error(self):
        with patch("cuegrid.audio.legacy_stems._load_ffmpeg") as load_ffmpeg:
            mock_ffmpeg = load_ffmpeg.return_value
            mock_stream = MagicMock()
            mock_ffmpeg.input.return_value = mock_stream
            mock_stream.output.return_value = mock_stream
            mock_stream.overwrite_output.return_value = mock_stream
            mock_stream.run.side_effect = RuntimeError("unexpected")

            with pytest.raises(RuntimeError):
                extract_drum_stem("fake_sidecar.stem.mp4")
