"""Tests for standalone batch metadata orchestration."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from cuegrid.audio import MetadataWriteError
from cuegrid.engine import (
    run_metadata_update_pipeline,
    validate_metadata_update_payload,
)
from cuegrid.nml.parser import NmlParser


def _write_collection(nml_path: Path, track_paths: list[Path]) -> None:
    entries: list[str] = []
    for index, track_path in enumerate(track_paths):
        absolute = str(track_path.resolve()).replace("\\", "/")
        drive, rest = absolute.split(":", 1)
        directory, _, filename = rest.rpartition("/")
        dir_attr = "/:" + directory.lstrip("/").replace("/", "/:") + "/:"
        entries.append(
            f'<ENTRY TITLE="Track {index}" ARTIST="Artist {index}">'
            f'<LOCATION VOLUME="{drive}:" DIR="{dir_attr}" FILE="{filename}" />'
            '<INFO PLAYTIME="60" /><TEMPO BPM="120" /></ENTRY>'
        )
    nml_path.write_text(
        f"<NML><COLLECTION>{''.join(entries)}</COLLECTION><PLAYLISTS /></NML>",
        encoding="utf-8",
    )


class TestMetadataUpdatePayload:
    def test_rejects_unknown_fields_before_loading_nml(self):
        with pytest.raises(ValueError, match="unsupported metadata"):
            validate_metadata_update_payload(
                {"track_paths": ["track.flac"], "fields": {"unknown": "x"}}
            )

    def test_rejects_non_integer_rating(self):
        with pytest.raises(ValueError, match="rating"):
            validate_metadata_update_payload(
                {"track_paths": ["track.flac"], "fields": {"rating": 3.5}}
            )


class TestMetadataUpdatePipeline:
    def test_commits_nml_before_best_effort_physical_writes(self, tmp_path):
        tracks = [tmp_path / "one.flac", tmp_path / "two.flac"]
        nml_path = tmp_path / "collection.nml"
        _write_collection(nml_path, tracks)
        payload = {"track_paths": [str(track) for track in tracks], "fields": {"genre": "Techno"}}
        starts: list[tuple[str, int, int]] = []
        nml_statuses: list[tuple[str, bool]] = []
        mutagen_statuses: list[tuple[str, bool, str | None]] = []

        with patch(
            "cuegrid.engine.pipeline.write_metadata_to_file",
            side_effect=[MetadataWriteError("locked"), None],
        ) as write_file:
            result = run_metadata_update_pipeline(
                nml_path,
                payload,
                write_to_files=True,
                on_track_start=lambda result, index, total: starts.append((result.path, index, total)),
                on_nml_status=lambda result: nml_statuses.append((result.path, result.nml_updated)),
                on_mutagen_status=lambda result: mutagen_statuses.append(
                    (result.path, result.physical_file_updated, result.error["message"] if result.error else None)
                ),
            )

        assert [item.nml_updated for item in result.results] == [True, True]
        assert result.results[0].error is not None
        assert result.results[0].error["code"] == "physical_write_failed"
        assert result.results[1].physical_file_updated is True
        assert write_file.call_count == 2
        assert starts == [(str(tracks[0]), 1, 2), (str(tracks[1]), 2, 2)]
        assert nml_statuses == [(str(tracks[0]), True), (str(tracks[1]), True)]
        assert mutagen_statuses[0] == (str(tracks[0]), False, "locked")
        assert mutagen_statuses[1] == (str(tracks[1]), True, None)

        collection = NmlParser(nml_path).get_library()["collection"]
        assert all(track["genre"] == "Techno" for track in collection.values())

    def test_unresolved_path_does_not_prevent_other_nml_updates(self, tmp_path):
        resolved = tmp_path / "resolved.flac"
        nml_path = tmp_path / "collection.nml"
        _write_collection(nml_path, [resolved])
        payload = {
            "track_paths": [str(tmp_path / "missing.flac"), str(resolved)],
            "fields": {"comment": "Updated"},
        }

        result = run_metadata_update_pipeline(nml_path, payload)

        assert result.results[0].error is not None
        assert result.results[0].error["code"] == "nml_resolution_failed"
        assert result.results[1].nml_updated is True
        row = next(iter(NmlParser(nml_path).get_library()["collection"].values()))
        assert row["comment"] == "Updated"

    def test_does_not_attempt_physical_writes_when_atomic_nml_write_fails(self, tmp_path):
        track = tmp_path / "track.flac"
        nml_path = tmp_path / "collection.nml"
        _write_collection(nml_path, [track])
        payload = {"track_paths": [str(track)], "fields": {"genre": "Techno"}}

        with patch(
            "cuegrid.engine.pipeline.NmlWriter.write_metadata_batch",
            side_effect=OSError("NML locked"),
        ), patch("cuegrid.engine.pipeline.write_metadata_to_file") as write_file:
            with pytest.raises(OSError, match="NML locked"):
                run_metadata_update_pipeline(nml_path, payload, write_to_files=True)

        write_file.assert_not_called()
