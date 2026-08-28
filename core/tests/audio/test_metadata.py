"""Format-contract coverage for physical metadata writing."""

from __future__ import annotations

import pytest
from mutagen.id3 import ID3

from cuegrid.audio import (
    UnsupportedAudioFormatError,
    write_metadata_to_file,
)


def test_mp3_writer_uses_id3v24_and_traktor_rating_owner(tmp_path):
    path = tmp_path / "tags.mp3"
    ID3().save(path)

    write_metadata_to_file(
        path,
        {
            "title": "Title",
            "producer": "Producer",
            "rating": 4,
        },
    )

    tags = ID3(path)
    assert tags.version == (2, 4, 0)
    assert tags["TIT2"].text == ["Title"]
    assert tags["TIPL"].people == [["producer", "Producer"]]
    assert tags["POPM:traktor@native-instruments.de"].rating == 196


def test_rejects_unsupported_physical_format(tmp_path):
    with pytest.raises(UnsupportedAudioFormatError):
        write_metadata_to_file(tmp_path / "track.ogg", {"title": "Title"})
