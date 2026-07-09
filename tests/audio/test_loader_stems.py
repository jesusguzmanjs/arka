"""Tests for the v2.0 stem-extraction addition to ``traktorco.audio.loader``.

Covers ``extract_drum_stem`` in isolation from real ``ffmpeg`` binaries by
mocking the ``ffmpeg-python`` call chain -- this module's job is only to
build the right ffmpeg invocation and manage the temporary file's
lifecycle, not to re-test ffmpeg itself.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import ffmpeg
import pytest

from traktorco.audio.loader import DRUMS_STEM_STREAM_INDEX, extract_drum_stem


class TestExtractDrumStem:
    def test_invokes_ffmpeg_with_expected_stream_map(self):
        with patch("traktorco.audio.loader.ffmpeg") as mock_ffmpeg:
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
        with patch("traktorco.audio.loader.ffmpeg") as mock_ffmpeg:
            mock_stream = MagicMock()
            mock_ffmpeg.input.return_value = mock_stream
            mock_stream.output.return_value = mock_stream
            mock_stream.overwrite_output.return_value = mock_stream

            result_path = extract_drum_stem("fake_sidecar.stem.mp4", stream_index=2)

            _, output_kwargs = mock_stream.output.call_args
            assert output_kwargs["map"] == "0:2"
            result_path.unlink(missing_ok=True)

    def test_cleans_up_temp_file_and_reraises_on_ffmpeg_error(self):
        with patch("traktorco.audio.loader.ffmpeg") as mock_ffmpeg:
            mock_ffmpeg.Error = ffmpeg.Error
            mock_stream = MagicMock()
            mock_ffmpeg.input.return_value = mock_stream
            mock_stream.output.return_value = mock_stream
            mock_stream.overwrite_output.return_value = mock_stream
            mock_stream.run.side_effect = ffmpeg.Error(
                "ffmpeg", b"", b"stream 0:1 not found"
            )

            with pytest.raises(ffmpeg.Error):
                extract_drum_stem("fake_sidecar.stem.mp4")

    def test_cleans_up_temp_file_on_unexpected_error(self):
        with patch("traktorco.audio.loader.ffmpeg") as mock_ffmpeg:
            mock_ffmpeg.Error = ffmpeg.Error
            mock_stream = MagicMock()
            mock_ffmpeg.input.return_value = mock_stream
            mock_stream.output.return_value = mock_stream
            mock_stream.overwrite_output.return_value = mock_stream
            mock_stream.run.side_effect = RuntimeError("unexpected")

            with pytest.raises(RuntimeError):
                extract_drum_stem("fake_sidecar.stem.mp4")
