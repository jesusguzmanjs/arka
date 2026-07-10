"""Tests for ``cuegrid.nml.stems``.

Covers native stem sidecar path prediction: the FLAGS bitmask check
(spec section 9.1), the ported hashing routine from
``zicez/traktor-stem-bridge`` (validated against that project's own
published fixtures for a zero TrackID and an incrementing-byte
TrackID), and the v2.1 "Smart Stems Path" root-resolution order (spec
section 9.6): explicit override -> ``Traktor Settings.tsi`` ->
native Music folder -> NML-sibling fallback.
"""

import base64
from pathlib import Path

import pytest
from cuegrid.nml.models import TempoInfo, TrackEntry
from cuegrid.nml.stems import (
    STEM_FLAG_BIT,
    TSI_SETTINGS_FILENAME,
    decode_audio_id,
    has_stem_flag,
    predict_sidecar,
    predict_sidecar_from_track_id,
    read_stems_dir_from_settings,
    resolve_stem_path,
)


def _audio_id_for(track_id: bytes) -> str:
    return base64.b64encode(track_id).decode()


def _entry(audio_id: str | None, flags: int | None) -> TrackEntry:
    return TrackEntry(
        title="Test",
        artist="Test Artist",
        location_path="/music/test.flac",
        tempo=TempoInfo(bpm=128.0),
        audio_id=audio_id,
        flags=flags,
    )


def _write_tsi(directory: Path, stems_dir_value: str | None) -> Path:
    """Write a minimal, valid ``Traktor Settings.tsi`` under ``directory``.

    Mirrors the real file's shape closely enough for parsing purposes:
    valid XML with an ``<Entry Name="..." Type="..." Value="...">`` node
    per setting.
    """
    tsi_path = directory / TSI_SETTINGS_FILENAME
    if stems_dir_value is None:
        # No GeneratedStems entry at all -- some other, unrelated entry.
        body = '<Entry Name="Some.Other.Setting" Type="1" Value="42"></Entry>'
    else:
        body = (
            f'<Entry Name="Browser.Dir.GeneratedStems" Type="3" '
            f'Value="{stems_dir_value}"></Entry>'
        )
    tsi_path.write_text(
        f"<NIXML>\n<TraktorSettings>\n{body}\n</TraktorSettings>\n</NIXML>",
        encoding="utf-8",
    )
    return tsi_path


class TestHasStemFlag:
    def test_true_when_bit_set(self):
        assert has_stem_flag(76) is True  # 76 = 12 | 0x40

    def test_false_when_bit_not_set(self):
        assert has_stem_flag(12) is False

    def test_false_when_flags_none(self):
        assert has_stem_flag(None) is False

    def test_flag_bit_is_0x40(self):
        assert STEM_FLAG_BIT == 0x40


class TestDecodeAudioId:
    def test_round_trips_256_bytes(self):
        track_id = bytes(range(256))
        decoded = decode_audio_id(_audio_id_for(track_id))
        assert decoded == track_id

    def test_tolerates_missing_padding(self):
        track_id = bytes(256)
        audio_id_no_padding = _audio_id_for(track_id).rstrip("=")
        assert decode_audio_id(audio_id_no_padding) == track_id


class TestPredictSidecar:
    """Validated against zicez/traktor-stem-bridge's published fixtures."""

    def test_zero_track_id(self):
        prediction = predict_sidecar_from_track_id(bytes(256))
        assert prediction.shard == 31
        assert prediction.basename == "5MO1STA4IXTHCA3NYWKDDKERCO3A"
        assert prediction.shard_dir == "031"
        assert prediction.filename == "5MO1STA4IXTHCA3NYWKDDKERCO3A.stem.mp4"

    def test_incrementing_track_id(self):
        prediction = predict_sidecar(_audio_id_for(bytes(range(256))))
        assert prediction.shard == 98
        assert prediction.basename == "CTPFBGASQA4Q5BRYNBCRBCEBXBLA"
        assert prediction.shard_dir == "098"

    def test_rejects_non_256_byte_track_id(self):
        with pytest.raises(ValueError, match="256 bytes"):
            predict_sidecar_from_track_id(b"too short")


class TestReadStemsDirFromSettings:
    """Covers the ``Traktor Settings.tsi`` parser (spec section 9.6)."""

    def test_returns_none_when_tsi_missing(self, tmp_path):
        nml_path = tmp_path / "collection.nml"
        assert read_stems_dir_from_settings(nml_path) is None

    def test_returns_none_when_tsi_is_malformed_xml(self, tmp_path):
        nml_path = tmp_path / "collection.nml"
        (tmp_path / TSI_SETTINGS_FILENAME).write_text(
            "<NIXML><TraktorSettings><Entry", encoding="utf-8"
        )
        assert read_stems_dir_from_settings(nml_path) is None

    def test_returns_none_when_entry_absent(self, tmp_path):
        nml_path = tmp_path / "collection.nml"
        _write_tsi(tmp_path, stems_dir_value=None)
        assert read_stems_dir_from_settings(nml_path) is None

    def test_returns_parsed_path_when_entry_present(self, tmp_path):
        nml_path = tmp_path / "collection.nml"
        _write_tsi(tmp_path, stems_dir_value=r"D:\TraktorData\Stems")
        result = read_stems_dir_from_settings(nml_path)
        assert result == Path(r"D:\TraktorData\Stems")

    def test_looks_for_tsi_next_to_nml_not_cwd(self, tmp_path):
        nested_nml_dir = tmp_path / "Traktor 4.5.0"
        nested_nml_dir.mkdir()
        nml_path = nested_nml_dir / "collection.nml"
        _write_tsi(nested_nml_dir, stems_dir_value="/mnt/music/Stems")

        # A .tsi in tmp_path itself (not the nml's parent) must be ignored.
        result = read_stems_dir_from_settings(nml_path)
        assert result == Path("/mnt/music/Stems")


class TestResolveStemPath:
    """Covers the v2.1 Smart Stems Path root-resolution order (spec 9.6):
    explicit override -> Traktor Settings.tsi -> native Music folder ->
    NML-sibling fallback.
    """

    _AUDIO_ID = _audio_id_for(bytes(256))
    _SHARD_DIR = "031"
    _FILENAME = "5MO1STA4IXTHCA3NYWKDDKERCO3A.stem.mp4"

    def test_returns_none_without_audio_id(self, monkeypatch, tmp_path):
        # Home has no native Music/Traktor/Stems dir -- irrelevant here since
        # a missing audio_id short-circuits before any root resolution.
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        entry = _entry(audio_id=None, flags=76)
        assert resolve_stem_path(entry, "C:/Traktor/collection.nml") is None

    def test_returns_none_without_audio_id_even_with_explicit_stems_dir(self):
        entry = _entry(audio_id=None, flags=76)
        result = resolve_stem_path(
            entry, "C:/Traktor/collection.nml", stems_dir="D:/CustomStems"
        )
        assert result is None

    def test_explicit_stems_dir_wins_over_tsi_and_native_music_folder(
        self, monkeypatch, tmp_path
    ):
        # Both a .tsi entry and the native Music/Traktor/Stems folder exist,
        # but an explicit override must win over both.
        fake_home = tmp_path / "home"
        (fake_home / "Music" / "Traktor" / "Stems").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        nml_dir = tmp_path / "Traktor 4.5.0"
        nml_dir.mkdir()
        _write_tsi(nml_dir, stems_dir_value=str(tmp_path / "TsiStems"))
        nml_path = nml_dir / "collection.nml"

        entry = _entry(audio_id=self._AUDIO_ID, flags=76)
        custom_dir = tmp_path / "CustomStems"
        result = resolve_stem_path(entry, nml_path, stems_dir=custom_dir)
        expected = custom_dir / self._SHARD_DIR / self._FILENAME
        assert result == expected

    def test_prefers_tsi_entry_over_native_music_folder(self, monkeypatch, tmp_path):
        # Native Music/Traktor/Stems exists too, but the .tsi is the
        # definitive source of truth and must win.
        fake_home = tmp_path / "home"
        (fake_home / "Music" / "Traktor" / "Stems").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        nml_dir = tmp_path / "Traktor 4.5.0"
        nml_dir.mkdir()
        tsi_stems_dir = tmp_path / "TsiStems"
        _write_tsi(nml_dir, stems_dir_value=str(tsi_stems_dir))
        nml_path = nml_dir / "collection.nml"

        entry = _entry(audio_id=self._AUDIO_ID, flags=76)
        result = resolve_stem_path(entry, nml_path)
        expected = tsi_stems_dir / self._SHARD_DIR / self._FILENAME
        assert result == expected

    def test_falls_back_to_native_music_folder_when_tsi_missing(
        self, monkeypatch, tmp_path
    ):
        fake_home = tmp_path / "home"
        music_stems = fake_home / "Music" / "Traktor" / "Stems"
        music_stems.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        # No Traktor Settings.tsi written next to the NML.
        entry = _entry(audio_id=self._AUDIO_ID, flags=76)
        result = resolve_stem_path(entry, "C:/Traktor 4.5.0/collection.nml")
        expected = music_stems / self._SHARD_DIR / self._FILENAME
        assert result == expected

    def test_falls_back_to_native_music_folder_when_tsi_has_no_entry(
        self, monkeypatch, tmp_path
    ):
        fake_home = tmp_path / "home"
        music_stems = fake_home / "Music" / "Traktor" / "Stems"
        music_stems.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        nml_dir = tmp_path / "Traktor 4.5.0"
        nml_dir.mkdir()
        _write_tsi(nml_dir, stems_dir_value=None)  # unrelated entry only
        nml_path = nml_dir / "collection.nml"

        entry = _entry(audio_id=self._AUDIO_ID, flags=76)
        result = resolve_stem_path(entry, nml_path)
        expected = music_stems / self._SHARD_DIR / self._FILENAME
        assert result == expected

    def test_falls_back_to_nml_sibling_when_tsi_and_native_music_folder_missing(
        self, monkeypatch, tmp_path
    ):
        # Home exists but has no Music/Traktor/Stems directory, and no .tsi
        # is present next to the NML either.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        entry = _entry(audio_id=self._AUDIO_ID, flags=76)
        result = resolve_stem_path(entry, "C:/Traktor 4.5.0/collection.nml")
        expected = Path("C:/Traktor 4.5.0") / "Stems" / self._SHARD_DIR / self._FILENAME
        assert result == expected
