"""Tests for the v2.0 Stems Integration interception in ``core.pipeline``.

Covers ``_resolve_analysis_source``'s graceful fallback chain (spec
section 9.3): no stem flag -> original audio; stem flag but sidecar
missing on disk -> original audio; stem flag and sidecar present ->
extracted drum stem; extraction failure -> original audio.
"""

import base64
from pathlib import Path
from unittest.mock import patch

from cuegrid.core.pipeline import _resolve_analysis_source
from cuegrid.nml.models import TempoInfo, TrackEntry


def _entry(audio_id: str | None, flags: int | None) -> TrackEntry:
    return TrackEntry(
        title="Test Track",
        artist="Test Artist",
        location_path="/music/test.flac",
        tempo=TempoInfo(bpm=128.0),
        audio_id=audio_id,
        flags=flags,
    )


_FAKE_AUDIO_ID = base64.b64encode(bytes(256)).decode()


class TestResolveAnalysisSource:
    def test_bypasses_stem_lookup_when_no_stems_is_enabled(self, tmp_path):
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=76)
        nml_path = tmp_path / "collection.nml"

        with patch("cuegrid.core.pipeline.has_stem_flag") as mock_has_flag:
            with patch("cuegrid.core.pipeline.resolve_stem_path") as mock_resolve:
                with patch("cuegrid.core.pipeline.extract_drum_stem") as mock_extract:
                    path, temp = _resolve_analysis_source(
                        entry,
                        "/music/test.flac",
                        nml_path,
                        no_stems=True,
                    )

        assert path == "/music/test.flac"
        assert temp is None
        mock_has_flag.assert_not_called()
        mock_resolve.assert_not_called()
        mock_extract.assert_not_called()

    def test_falls_back_when_stem_flag_absent(self):
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=12)  # no 0x40 bit
        path, temp = _resolve_analysis_source(
            entry, "/music/test.flac", "/collection/collection.nml"
        )
        assert path == "/music/test.flac"
        assert temp is None

    def test_falls_back_when_flags_missing(self):
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=None)
        path, temp = _resolve_analysis_source(
            entry, "/music/test.flac", "/collection/collection.nml"
        )
        assert path == "/music/test.flac"
        assert temp is None

    def test_falls_back_when_sidecar_not_on_disk(self, tmp_path):
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=76)
        nml_path = tmp_path / "collection.nml"
        # No Stems/ directory created -- predicted sidecar won't exist.
        path, temp = _resolve_analysis_source(entry, "/music/test.flac", nml_path)
        assert path == "/music/test.flac"
        assert temp is None

    def test_falls_back_when_audio_id_missing(self, tmp_path):
        entry = _entry(audio_id=None, flags=76)
        nml_path = tmp_path / "collection.nml"
        path, temp = _resolve_analysis_source(entry, "/music/test.flac", nml_path)
        assert path == "/music/test.flac"
        assert temp is None

    def test_uses_extracted_drum_stem_when_sidecar_exists(self, tmp_path):
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=76)
        nml_path = tmp_path / "collection.nml"

        with patch("cuegrid.core.pipeline.resolve_stem_path") as mock_resolve:
            fake_sidecar = tmp_path / "fake.stem.mp4"
            fake_sidecar.write_bytes(b"fake")
            mock_resolve.return_value = fake_sidecar

            fake_wav = tmp_path / "extracted.wav"
            fake_wav.write_bytes(b"fake wav")
            with patch(
                "cuegrid.core.pipeline.extract_drum_stem", return_value=fake_wav
            ) as mock_extract:
                path, temp = _resolve_analysis_source(
                    entry, "/music/test.flac", nml_path
                )

            mock_extract.assert_called_once_with(fake_sidecar)
            assert path == fake_wav
            assert temp == fake_wav

    def test_falls_back_when_extraction_fails(self, tmp_path):
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=76)
        nml_path = tmp_path / "collection.nml"

        with patch("cuegrid.core.pipeline.resolve_stem_path") as mock_resolve:
            fake_sidecar = tmp_path / "fake.stem.mp4"
            fake_sidecar.write_bytes(b"fake")
            mock_resolve.return_value = fake_sidecar

            with patch(
                "cuegrid.core.pipeline.extract_drum_stem",
                side_effect=RuntimeError("ffmpeg exploded"),
            ):
                path, temp = _resolve_analysis_source(
                    entry, "/music/test.flac", nml_path
                )

            assert path == "/music/test.flac"
            assert temp is None

    def test_forwards_stems_dir_override_to_resolve_stem_path(self, tmp_path):
        # v2.1 Smart Stems Path: an explicit stems_dir override must be
        # forwarded through to nml.stems.resolve_stem_path unchanged.
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=76)
        nml_path = tmp_path / "collection.nml"
        custom_stems_dir = tmp_path / "CustomStems"

        with patch(
            "cuegrid.core.pipeline.resolve_stem_path", return_value=None
        ) as mock_resolve:
            _resolve_analysis_source(
                entry, "/music/test.flac", nml_path, stems_dir=custom_stems_dir
            )

        mock_resolve.assert_called_once_with(
            entry, nml_path, stems_dir=custom_stems_dir
        )

    def test_defaults_stems_dir_to_none_when_not_given(self, tmp_path):
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=76)
        nml_path = tmp_path / "collection.nml"

        with patch(
            "cuegrid.core.pipeline.resolve_stem_path", return_value=None
        ) as mock_resolve:
            _resolve_analysis_source(entry, "/music/test.flac", nml_path)

        mock_resolve.assert_called_once_with(entry, nml_path, stems_dir=None)

    def test_falls_back_when_extracted_stem_is_empty(self, tmp_path):
        """v2.2 Empty Stem Detection (spec section 10.1): an extracted drum
        stem that is practically silent/ambient must fall back to the
        original Master audio, and the temp WAV must be cleaned up.
        """
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=76)
        nml_path = tmp_path / "collection.nml"

        with patch("cuegrid.core.pipeline.resolve_stem_path") as mock_resolve:
            fake_sidecar = tmp_path / "fake.stem.mp4"
            fake_sidecar.write_bytes(b"fake")
            mock_resolve.return_value = fake_sidecar

            fake_wav = tmp_path / "extracted.wav"
            fake_wav.write_bytes(b"fake wav")
            with patch(
                "cuegrid.core.pipeline.extract_drum_stem", return_value=fake_wav
            ):
                with patch(
                    "cuegrid.core.pipeline.is_drum_stem_empty", return_value=True
                ) as mock_is_empty:
                    path, temp = _resolve_analysis_source(
                        entry, "/music/test.flac", nml_path
                    )

            mock_is_empty.assert_called_once_with(fake_wav)
            assert path == "/music/test.flac"
            assert temp is None
            assert not fake_wav.exists()  # temp WAV must be cleaned up

    def test_uses_extracted_stem_when_not_empty(self, tmp_path):
        """Non-empty stems are used as before -- the empty-stem guard must
        not affect the existing success path.
        """
        entry = _entry(audio_id=_FAKE_AUDIO_ID, flags=76)
        nml_path = tmp_path / "collection.nml"

        with patch("cuegrid.core.pipeline.resolve_stem_path") as mock_resolve:
            fake_sidecar = tmp_path / "fake.stem.mp4"
            fake_sidecar.write_bytes(b"fake")
            mock_resolve.return_value = fake_sidecar

            fake_wav = tmp_path / "extracted.wav"
            fake_wav.write_bytes(b"fake wav")
            with patch(
                "cuegrid.core.pipeline.extract_drum_stem", return_value=fake_wav
            ):
                with patch(
                    "cuegrid.core.pipeline.is_drum_stem_empty", return_value=False
                ):
                    path, temp = _resolve_analysis_source(
                        entry, "/music/test.flac", nml_path
                    )

            assert path == fake_wav
            assert temp == fake_wav
