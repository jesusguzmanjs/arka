"""Tests for ``cuegrid.cli``.

Covers the CLI features added on top of ``core.pipeline`` (spec section
7.3, step 6): ``--title``/``--artist`` disambiguation flags, OS-aware
auto-discovery of the default ``collection.nml`` when ``--nml`` is
omitted, and the ``AppConfig`` tuning flags (e.g. ``--phrase-beats``,
``--energy-threshold``, ``--timbre-threshold``) that override individual
dataclass defaults on a per-flag basis.
"""

from __future__ import annotations

import json
import pytest
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from cuegrid import cli
from cuegrid.config import AppConfig
from cuegrid.engine import (
    BatchResult,
    BatchSaveResult,
    BatchSaveTrackResult,
    PipelineResult,
)
from cuegrid.nml.constants import CueType
from cuegrid.nml.models import TempoInfo, TrackEntry
from cuegrid.nml.parser import AmbiguousTrackError, NmlParser, TrackNotFoundError
from tests.fixtures import generate_synthetic_fixture as fixture_gen

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_COLLECTION = FIXTURES_DIR / "sample_collection.nml"
KNOWN_TRACK_PATH = r"C:\Users\ska_m\Music\Tidal\Machinedrum - NO 1 KNEW.flac"


# --------------------------------------------------------------------------
# discover_collection_nml_paths / discover_default_nml_path
# --------------------------------------------------------------------------


def _touch_nml(path: Path, mtime_offset_sec: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<NML/>", encoding="utf-8")
    if mtime_offset_sec:
        now = time.time()
        import os

        os.utime(path, (now + mtime_offset_sec, now + mtime_offset_sec))
    return path


class TestDiscoverCollectionNmlPaths:
    def test_finds_collection_nml_under_versioned_traktor_dirs(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(cli.platform, "system", lambda: "Windows")

        expected = _touch_nml(
            tmp_path
            / "Documents"
            / "Native Instruments"
            / "Traktor 3.5.0"
            / "collection.nml"
        )

        found = cli.discover_collection_nml_paths()
        assert expected in found

    def test_returns_empty_list_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(cli.platform, "system", lambda: "Windows")

        assert cli.discover_collection_nml_paths() == []

    def test_finds_multiple_versions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(cli.platform, "system", lambda: "Windows")

        ni_root = tmp_path / "Documents" / "Native Instruments"
        v1 = _touch_nml(ni_root / "Traktor 3.5.0" / "collection.nml")
        v2 = _touch_nml(ni_root / "Traktor 3.6.0" / "collection.nml")

        found = cli.discover_collection_nml_paths()
        assert set(found) == {v1, v2}


class TestDiscoverDefaultNmlPath:
    def test_returns_none_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(cli.platform, "system", lambda: "Windows")

        assert cli.discover_default_nml_path() is None

    def test_picks_most_recently_modified_when_multiple_versions_exist(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(cli.platform, "system", lambda: "Windows")

        ni_root = tmp_path / "Documents" / "Native Instruments"
        older = _touch_nml(
            ni_root / "Traktor 3.5.0" / "collection.nml", mtime_offset_sec=-1000
        )
        newer = _touch_nml(
            ni_root / "Traktor 3.6.0" / "collection.nml", mtime_offset_sec=0
        )

        assert cli.discover_default_nml_path() == newer
        assert cli.discover_default_nml_path() != older


class TestResolveNmlPath:
    def test_explicit_path_takes_precedence_over_discovery(self, tmp_path, monkeypatch):
        explicit = tmp_path / "explicit.nml"
        monkeypatch.setattr(
            cli, "discover_default_nml_path", lambda: tmp_path / "discovered.nml"
        )
        assert cli._resolve_nml_path(explicit) == explicit

    def test_falls_back_to_discovery_when_not_provided(self, tmp_path, monkeypatch):
        discovered = tmp_path / "discovered.nml"
        monkeypatch.setattr(cli, "discover_default_nml_path", lambda: discovered)
        assert cli._resolve_nml_path(None) == discovered

    def test_returns_none_when_discovery_finds_nothing(self, monkeypatch):
        monkeypatch.setattr(cli, "discover_default_nml_path", lambda: None)
        assert cli._resolve_nml_path(None) is None


class TestCompileSmartPlaylist:
    def test_parser_registers_smart_playlist_payload(self):
        payload = '{"name":"Fast","match":"all","rules":[{"field":"bpm","operator":"greater_than","value":120}]}'

        args = cli.build_parser().parse_args(["--compile-smart-playlist", payload])

        assert args.compile_smart_playlist == payload

    def test_compiles_matching_entries_and_emits_json_result(
        self, tmp_path, capsys
    ):
        nml_path = tmp_path / "collection.nml"
        shutil.copy2(SAMPLE_COLLECTION, nml_path)
        payload = {
            "name": "Fast tracks",
            "match": "all",
            "rules": [
                {"field": "bpm", "operator": "greater_than", "value": 120}
            ],
        }

        exit_code = cli.main(
            [
                "--compile-smart-playlist",
                json.dumps(payload),
                "--nml",
                str(nml_path),
                "--json",
            ]
        )

        assert exit_code == 0
        response = json.loads(capsys.readouterr().out)
        assert response["ok"] is True
        assert response["result"] == {
            "type": "smart_playlist_compiled",
            "name": "Fast tracks",
            "matched": 1,
            "uuid": response["uuid"],
        }
        assert response["type"] == "smart_playlist_compiled"

        root = ET.parse(nml_path).getroot()
        playlist = root.find(
            "./PLAYLISTS/NODE[@TYPE='FOLDER'][@NAME='$ROOT']/SUBNODES/"
            "NODE[@TYPE='PLAYLIST'][@NAME='Fast tracks']/PLAYLIST"
        )
        assert playlist is not None
        assert playlist.get("ENTRIES") == "1"

    def test_reports_invalid_payload_as_json_error(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        shutil.copy2(SAMPLE_COLLECTION, nml_path)

        exit_code = cli.main(
            ["--compile-smart-playlist", "not json", "--nml", str(nml_path), "--json"]
        )

        assert exit_code == 1
        response = json.loads(capsys.readouterr().out)
        assert response["ok"] is False
        assert "error" in response

    def test_rejects_zero_matches_without_writing_the_nml(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        shutil.copy2(SAMPLE_COLLECTION, nml_path)
        original_contents = nml_path.read_bytes()
        payload = {
            "name": "No matches",
            "match": "all",
            "rules": [
                {"field": "bpm", "operator": "greater_than", "value": 999}
            ],
        }

        exit_code = cli.main(
            [
                "--compile-smart-playlist",
                json.dumps(payload),
                "--nml",
                str(nml_path),
                "--json",
            ]
        )

        assert exit_code == 1
        assert json.loads(capsys.readouterr().out) == {
            "ok": False,
            "error": "No tracks match these rules. Adjust your filters and try again.",
        }
        assert nml_path.read_bytes() == original_contents


class TestCreateStaticPlaylist:
    def test_creates_an_empty_playlist_and_emits_json_result(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        shutil.copy2(SAMPLE_COLLECTION, nml_path)

        exit_code = cli.main(
            [
                "--create-static-playlist",
                json.dumps({"name": "Empty crate", "entries": []}),
                "--nml",
                str(nml_path),
                "--json",
            ]
        )

        assert exit_code == 0
        response = json.loads(capsys.readouterr().out)
        assert response["type"] == "static_playlist_created"
        assert response["name"] == "Empty crate"
        assert response["entries"] == 0

        root = ET.parse(nml_path).getroot()
        playlist = root.find(
            "./PLAYLISTS/NODE[@TYPE='FOLDER'][@NAME='$ROOT']/SUBNODES/"
            "NODE[@TYPE='PLAYLIST'][@NAME='Empty crate']/PLAYLIST"
        )
        assert playlist is not None
        assert playlist.get("ENTRIES") == "0"
        assert playlist.findall("ENTRY") == []


# --------------------------------------------------------------------------
# main(): argument passthrough and graceful error handling
# --------------------------------------------------------------------------


def _fake_result(
    title: str = "NO 1 KNEW", artist: str = "Machinedrum"
) -> PipelineResult:
    entry = TrackEntry(
        title=title,
        artist=artist,
        location_path="c:/fake/path.flac",
        tempo=TempoInfo(bpm=140.0),
        cues=[],
        grid_anchor_ms=0.0,
        duration_ms=10_000.0,
    )
    return PipelineResult(entry=entry, detected_events=[], written_cues=[])


class TestExportGui:
    def test_parser_registers_export_gui(self):
        args = cli.build_parser().parse_args(["track.mp3", "--export-gui"])

        assert args.export_gui is True
        assert args.json is False

    def test_emits_one_json_document_without_stdout_logs(self, tmp_path, monkeypatch, capsys):
        captured_kwargs = {}

        def fake_run_pipeline(**kwargs):
            captured_kwargs.update(kwargs)
            return _fake_result()

        monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        exit_code = cli.main(
            ["track.mp3", "--nml", str(nml_path), "--export-gui", "--verbose"]
        )

        assert exit_code == 0
        output = capsys.readouterr()
        payload = json.loads(output.out)
        assert output.out.count("\n") == 1
        assert payload == {
            "track_path": str(Path("track.mp3").resolve()),
            "bpm": 140.0,
            "grid_anchor_ms": 0.0,
            "is_flex_grid": False,
            "duration_ms": 10_000.0,
            "cues": [],
        }
        assert captured_kwargs["track_path"] == Path("track.mp3")

    def test_rejects_ndjson_combination(self, capsys):
        assert cli.main(["track.mp3", "--export-gui", "--json"]) == 1
        assert "cannot be combined" in capsys.readouterr().err


class TestMainArgumentPassthrough:
    def test_passes_multiple_positional_paths_to_batch_pipeline(
        self, tmp_path, monkeypatch
    ):
        captured_kwargs = {}

        def fake_run_batch_pipeline(**kwargs):
            captured_kwargs.update(kwargs)
            return BatchResult(results=[])

        monkeypatch.setattr(cli, "run_batch_pipeline", fake_run_batch_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        exit_code = cli.main(
            ["first.mp3", "second.mp3", "--nml", str(nml_path)]
        )

        assert exit_code == 0
        assert captured_kwargs["track_paths"] == ["first.mp3", "second.mp3"]

    def test_emits_flex_grid_skip_event_in_json_mode(self, tmp_path, monkeypatch, capsys):
        def fake_run_pipeline(**kwargs):
            result = _fake_result()
            result.skipped_reason = "flex_grid"
            return result

        monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        assert cli.main(["track.mp3", "--nml", str(nml_path), "--json"]) == 0

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert {"type": "skipped", "reason": "flex_grid"} in messages
        assert messages[-1] == {"type": "summary", "total": 1, "succeeded": 0, "skipped": 1}

    def test_passes_title_and_artist_to_pipeline(self, tmp_path, monkeypatch, capsys):
        captured_kwargs = {}

        def fake_run_pipeline(**kwargs):
            captured_kwargs.update(kwargs)
            return _fake_result()

        monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        exit_code = cli.main(
            [
                "track.mp3",
                "--nml",
                str(nml_path),
                "--title",
                "NO 1 KNEW",
                "--artist",
                "Machinedrum",
            ]
        )

        assert exit_code == 0
        assert captured_kwargs["title"] == "NO 1 KNEW"
        assert captured_kwargs["artist"] == "Machinedrum"
        assert captured_kwargs["nml_path"] == nml_path

    def test_title_and_artist_default_to_none(self, tmp_path, monkeypatch):
        captured_kwargs = {}

        def fake_run_pipeline(**kwargs):
            captured_kwargs.update(kwargs)
            return _fake_result()

        monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        cli.main(["track.mp3", "--nml", str(nml_path)])

        assert captured_kwargs["title"] is None
        assert captured_kwargs["artist"] is None

    def test_reports_ambiguous_track_error_gracefully(
        self, tmp_path, monkeypatch, capsys
    ):
        def fake_run_pipeline(**kwargs):
            raise AmbiguousTrackError("2 ENTRY elements matched path: ...")

        monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        exit_code = cli.main(["track.mp3", "--nml", str(nml_path)])

        assert exit_code == 1
        stderr = capsys.readouterr().err
        assert "--title" in stderr
        assert "--artist" in stderr

    def test_reports_track_not_found_gracefully(self, tmp_path, monkeypatch, capsys):
        def fake_run_pipeline(**kwargs):
            raise TrackNotFoundError("No ENTRY found matching path: ...")

        monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        exit_code = cli.main(["track.mp3", "--nml", str(nml_path)])

        assert exit_code == 1
        assert "error" in capsys.readouterr().err.lower()

    def test_exits_gracefully_when_no_nml_found_and_none_provided(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(cli, "discover_default_nml_path", lambda: None)

        exit_code = cli.main(["track.mp3"])

        assert exit_code == 1
        stderr = capsys.readouterr().err
        assert "--nml" in stderr

    def test_uses_discovered_nml_when_not_explicitly_provided(
        self, tmp_path, monkeypatch
    ):
        discovered = tmp_path / "discovered.nml"
        discovered.write_text("<NML/>", encoding="utf-8")
        monkeypatch.setattr(cli, "discover_default_nml_path", lambda: discovered)

        captured_kwargs = {}

        def fake_run_pipeline(**kwargs):
            captured_kwargs.update(kwargs)
            return _fake_result()

        monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

        cli.main(["track.mp3"])

        assert captured_kwargs["nml_path"] == discovered


# --------------------------------------------------------------------------
# --batch-save: unified atomic track mutation with compact NDJSON output.
# --------------------------------------------------------------------------


class TestRemixSetQueries:
    def test_list_and_get_bypass_analysis_and_emit_json(self, tmp_path, monkeypatch, capsys):
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text(
            """<NML><SETS><SET TITLE="Live Set" QUANT_VALUE="4" QUANT_STATE="1">
<TEMPO BPM="120" /><SLOT KEYLOCK="0" PUNCHMODE="1" FXENABLE="1" />
</SET></SETS></NML>""",
            encoding="utf-8",
        )

        def fail_analysis(**_kwargs):
            raise AssertionError("Remix Set query must not start audio analysis")

        monkeypatch.setattr(cli, "run_pipeline", fail_analysis)
        monkeypatch.setattr(cli, "run_batch_pipeline", fail_analysis)

        with pytest.raises(SystemExit) as list_exit:
            cli.main(["--nml", str(nml_path), "--list-remix-sets"])
        assert list_exit.value.code == 0
        assert json.loads(capsys.readouterr().out) == ["Live Set"]

        with pytest.raises(SystemExit) as get_exit:
            cli.main(["--nml", str(nml_path), "--get-remix-set", "Live Set"])
        assert get_exit.value.code == 0
        assert json.loads(capsys.readouterr().out) == {
            "title": "Live Set",
            "bpm": 120.0,
            "quantize_value": 4,
            "quantize_state": 1,
            "columns": [{"keylock": 0, "punchmode": 1, "fxenable": 1}],
            "pads": [],
        }

    def test_get_reports_not_found_as_json_and_exits_one(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML><SETS /></NML>", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--nml", str(nml_path), "--get-remix-set", "Missing"])

        assert exc_info.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "not_found"
        assert "Missing" in payload["message"]

    def test_list_and_get_are_mutually_exclusive(self):
        with pytest.raises(SystemExit) as exc_info:
            cli.build_parser().parse_args(
                ["--list-remix-sets", "--get-remix-set", "Live Set"]
            )

        assert exc_info.value.code == 2


class TestSaveRemixSet:
    def test_routes_json_payload_to_writer(self, tmp_path, monkeypatch, capsys):
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_write_remix_set(self, payload):
            captured["payload"] = payload

        monkeypatch.setattr(cli.NmlWriter, "write_remix_set", fake_write_remix_set)
        payload = '{"title":"Set","pads":[]}'

        assert cli.main(["--nml", str(nml_path), "--save-remix-set", payload]) == 0
        assert captured["payload"] == json.loads(payload)
        assert json.loads(capsys.readouterr().out) == {
            "ok": True,
            "message": "Remix Set saved successfully!",
        }

    def test_rejects_incompatible_operation(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        assert cli.main(
            [
                "--nml",
                str(nml_path),
                "--save-remix-set",
                "{}",
                "--batch-save",
                "{}",
            ]
        ) == 1
        assert "cannot be combined" in capsys.readouterr().err

    def test_rejects_audio_analysis_selector(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        assert cli.main(
            ["--nml", str(nml_path), "--playlist", "Warmup", "--save-remix-set", "{}"]
        ) == 1
        assert "cannot be combined" in capsys.readouterr().err


class TestBatchSave:
    def test_routes_payload_and_emits_ndjson(self, tmp_path, monkeypatch, capsys):
        captured: dict[str, object] = {}

        def fake_pipeline(**kwargs):
            captured.update(kwargs)
            track_result = BatchSaveTrackResult(
                path="track.flac",
                nml_updated=True,
                physical_file_updated=True,
            )
            return BatchSaveResult(results=[track_result])

        monkeypatch.setattr(cli, "run_batch_save_pipeline", fake_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")
        payload = '{"tracks":[{"path":"track.flac","metadata":{"genre":"Techno"}}]}'

        assert cli.main(["--nml", str(nml_path), "--batch-save", payload, "--write-to-files", "--json"]) == 0

        assert captured["write_to_files"] is True
        assert captured["payload"] == json.loads(payload)
        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert [message["type"] for message in messages] == [
            "nml_resolved",
            "batch_save_validated",
            "batch_save_nml_committed",
            "batch_save_track_complete",
            "batch_save_physical_status",
            "batch_save_summary",
        ]

    def test_rejects_analysis_selector(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")
        payload = '{"tracks":[{"path":"track.flac","metadata":{"genre":"Techno"}}]}'

        assert cli.main(["track.flac", "--nml", str(nml_path), "--batch-save", payload]) == 1
        assert "cannot be combined" in capsys.readouterr().err


# --------------------------------------------------------------------------
# --get-playlist-tracks (spec .openspec/4-library-spec.md section 1): a
# standalone, one-shot tracklist query for the GUI Library Browser. Bypasses
# audio analysis entirely, mirroring --list-playlists/--get-track-metadata's
# own interception pattern in main().
# --------------------------------------------------------------------------


class TestGetPlaylistTracks:
    def test_prints_json_array_of_tracks_and_exits_zero(self, capsys):
        # main() calls sys.exit(0) on this path (matching --list-playlists/
        # --get-track-metadata's own SystemExit-on-success convention), so
        # a successful run must be caught the same way as the error paths.
        with pytest.raises(SystemExit) as exc_info:
            cli.main(
                [
                    "--nml",
                    str(SAMPLE_COLLECTION),
                    "--get-playlist-tracks",
                    "prueba",
                ]
            )

        assert exc_info.value.code == 0
        stdout = capsys.readouterr().out.strip()
        tracks = json.loads(stdout)

        assert isinstance(tracks, list)
        assert len(tracks) == 2
        for track in tracks:
            assert set(track.keys()) == {"artist", "title", "location_path", "flags", "is_flex_grid"}
        titles = {t["title"] for t in tracks}
        assert "NO 1 KNEW" in titles
        assert "Doesn't Just Happen" in titles

    def test_no_pipeline_or_logging_side_effects(self, monkeypatch, capsys):
        # No audio analysis, no NDJSON/log decoration: run_pipeline and
        # run_batch_pipeline must never be invoked on this path.
        def _fail(*args, **kwargs):
            raise AssertionError("audio pipeline must not run for --get-playlist-tracks")

        monkeypatch.setattr(cli, "run_pipeline", _fail)
        monkeypatch.setattr(cli, "run_batch_pipeline", _fail)

        with pytest.raises(SystemExit) as exc_info:
            cli.main(
                [
                    "--nml",
                    str(SAMPLE_COLLECTION),
                    "--get-playlist-tracks",
                    "prueba",
                ]
            )

        assert exc_info.value.code == 0
        stderr = capsys.readouterr().err
        assert stderr == ""

    def test_reports_not_found_error_schema_and_exits_one(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            cli.main(
                [
                    "--nml",
                    str(SAMPLE_COLLECTION),
                    "--get-playlist-tracks",
                    "this-playlist-does-not-exist",
                ]
            )

        assert exc_info.value.code == 1
        stdout = capsys.readouterr().out.strip()
        payload = json.loads(stdout)

        assert payload["error"] == "not_found"
        assert "this-playlist-does-not-exist" in payload["message"]

    def test_reports_ambiguous_error_schema_and_exits_one(self, tmp_path, capsys):
        nml_content = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="0"></COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="2">
<NODE TYPE="PLAYLIST" NAME="dup"><PLAYLIST ENTRIES="0" TYPE="LIST" UUID="uuid1"></PLAYLIST></NODE>
<NODE TYPE="PLAYLIST" NAME="dup"><PLAYLIST ENTRIES="0" TYPE="LIST" UUID="uuid2"></PLAYLIST></NODE>
</SUBNODES></NODE></PLAYLISTS>
</NML>"""
        nml_file = tmp_path / "dup_playlist.nml"
        nml_file.write_text(nml_content, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            cli.main(
                ["--nml", str(nml_file), "--get-playlist-tracks", "dup"]
            )

        assert exc_info.value.code == 1
        stdout = capsys.readouterr().out.strip()
        payload = json.loads(stdout)

        assert payload["error"] == "ambiguous"
        assert "dup" in payload["message"]

    def test_returns_empty_array_for_empty_playlist(self, tmp_path, capsys):
        nml_content = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="0"></COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="1">
<NODE TYPE="PLAYLIST" NAME="empty"><PLAYLIST ENTRIES="0" TYPE="LIST" UUID="uuid1"></PLAYLIST></NODE>
</SUBNODES></NODE></PLAYLISTS>
</NML>"""
        nml_file = tmp_path / "empty_playlist.nml"
        nml_file.write_text(nml_content, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--nml", str(nml_file), "--get-playlist-tracks", "empty"])

        assert exc_info.value.code == 0
        stdout = capsys.readouterr().out.strip()
        assert json.loads(stdout) == []

    def test_exits_gracefully_when_no_nml_found(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "discover_default_nml_path", lambda: None)

        exit_code = cli.main(["--get-playlist-tracks", "prueba"])

        assert exit_code == 1
        stderr = capsys.readouterr().err
        assert "--nml" in stderr


# --------------------------------------------------------------------------
# Real end-to-end integration: --title actually resolves ambiguity and
# writes cues to the correct ENTRY only.
# --------------------------------------------------------------------------


def _write_duplicate_collection_for_synthetic_track(
    nml_path: Path, track_path: Path
) -> None:
    """Two ENTRYs sharing one LOCATION (the synthetic track), differing TITLE.

    Both share the synthetic fixture's real BPM/grid/duration so
    detect_events runs identically regardless of which ENTRY is chosen --
    the only observable difference is *which* ENTRY receives the written
    cues, proving --title actually threads through the whole pipeline.
    """
    track_str = str(track_path.resolve()).replace("\\", "/")
    drive, rest = track_str.split(":", 1)
    dir_part, _, file_part = rest.rpartition("/")
    dir_attr = "/:" + dir_part.lstrip("/").replace("/", "/:") + "/:"

    def entry_xml(title: str) -> str:
        return f"""<ENTRY TITLE="{title}" ARTIST="Test Artist"><LOCATION DIR="{dir_attr}" FILE="{file_part}" VOLUME="{drive}:" VOLUMEID="x"></LOCATION>
<INFO PLAYTIME="{fixture_gen.DURATION_MS / 1000:.0f}"></INFO>
<TEMPO BPM="{fixture_gen.BPM:.6f}" BPM_QUALITY="100.000000"></TEMPO>
<CUE_V2 NAME="AutoGrid" DISPL_ORDER="0" TYPE="4" START="{fixture_gen.GRID_ANCHOR_MS:.6f}" LEN="0.000000" REPEATS="-1" HOTCUE="-1"></CUE_V2>
</ENTRY>"""

    nml_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>
<COLLECTION ENTRIES="2">
{entry_xml("Track A")}
{entry_xml("Track B")}
</COLLECTION>
</NML>""",
        encoding="utf-8",
    )


class TestEndToEndDisambiguation:
    def test_title_flag_resolves_ambiguity_and_writes_only_the_chosen_entry(
        self, tmp_path, capsys
    ):
        track_path = fixture_gen.generate(tmp_path / "sample_track.wav")
        nml_path = tmp_path / "duplicate_collection.nml"
        _write_duplicate_collection_for_synthetic_track(nml_path, track_path)

        exit_code = cli.main(
            [str(track_path), "--nml", str(nml_path), "--title", "Track B"]
        )

        assert exit_code == 0
        stdout = capsys.readouterr().out
        assert "Track B" in stdout

        reparsed = NmlParser(nml_path)
        entry_b = reparsed.find_entry(track_path, title="Track B")
        entry_a = reparsed.find_entry(track_path, title="Track A")

        # Track B (the chosen entry) got new cues; Track A did not.
        assert any(c.type == CueType.CUE for c in entry_b.cues)
        assert not any(c.type == CueType.CUE for c in entry_a.cues)

    def test_without_title_flag_ambiguity_is_reported_and_nothing_is_written(
        self, tmp_path, capsys
    ):
        track_path = fixture_gen.generate(tmp_path / "sample_track.wav")
        nml_path = tmp_path / "duplicate_collection.nml"
        _write_duplicate_collection_for_synthetic_track(nml_path, track_path)
        original_bytes = nml_path.read_bytes()

        exit_code = cli.main([str(track_path), "--nml", str(nml_path)])

        assert exit_code == 1
        assert nml_path.read_bytes() == original_bytes  # untouched


# --------------------------------------------------------------------------
# AppConfig tuning flags: an omitted flag falls back to the dataclass
# default; a supplied flag overrides only that one field.
# --------------------------------------------------------------------------


class TestBuildConfigFromArgs:
    def test_no_flags_produces_pure_defaults(self):
        args = cli.build_parser().parse_args(["track.mp3", "--nml", "collection.nml"])
        config = cli.build_config_from_args(args)
        assert config == AppConfig()

    @pytest.mark.parametrize(
        ("mode", "energy", "timbre", "relative_confidence"),
        [
            ("soft", 2.0, 8.0, 0.15),
            ("medium", 4.0, 18.0, 0.30),
            ("hard", 7.0, 30.0, 0.50),
        ],
    )
    def test_mode_binds_all_three_sensitivity_thresholds(
        self, mode, energy, timbre, relative_confidence
    ):
        args = cli.build_parser().parse_args(
            ["track.mp3", "--nml", "collection.nml", "--mode", mode]
        )
        config = cli.build_config_from_args(args)

        assert config.detection_mode == mode
        assert config.energy_change_threshold_db == energy
        assert config.timbre_change_distance_threshold == timbre
        assert config.relative_confidence_threshold == relative_confidence

    def test_mode_overrides_all_individual_threshold_flags(self):
        args = cli.build_parser().parse_args(
            [
                "track.mp3",
                "--nml",
                "collection.nml",
                "--mode",
                "hard",
                "--energy-threshold",
                "1.0",
                "--timbre-threshold",
                "2.0",
                "--relative-confidence-threshold",
                "0.01",
            ]
        )
        config = cli.build_config_from_args(args)

        assert config.energy_change_threshold_db == 7.0
        assert config.timbre_change_distance_threshold == 30.0
        assert config.relative_confidence_threshold == 0.50

    def test_single_flag_overrides_only_that_field(self):
        args = cli.build_parser().parse_args(
            ["track.mp3", "--nml", "collection.nml", "--energy-threshold", "5.5"]
        )
        config = cli.build_config_from_args(args)

        assert config.energy_change_threshold_db == 5.5
        # Every other field must still be the untouched dataclass default.
        defaults = AppConfig()
        assert config.phrase_beats == defaults.phrase_beats
        assert (
            config.timbre_change_distance_threshold
            == defaults.timbre_change_distance_threshold
        )
        assert config.window_beats == defaults.window_beats

    def test_multiple_flags_override_independently(self):
        args = cli.build_parser().parse_args(
            [
                "track.mp3",
                "--nml",
                "collection.nml",
                "--phrase-beats",
                "8",
                "--timbre-threshold",
                "20.0",
                "--max-cues",
                "1",
            ]
        )
        config = cli.build_config_from_args(args)

        assert config.phrase_beats == 8
        assert config.timbre_change_distance_threshold == 20.0
        assert config.max_cues == 1
        # Untouched fields still default.
        assert (
            config.energy_change_threshold_db == AppConfig().energy_change_threshold_db
        )
        assert config.major_phrase_multiple == AppConfig().major_phrase_multiple

    @pytest.mark.parametrize(
        ("flag", "value", "field_name", "expected"),
        [
            ("--phrase-beats", "32", "phrase_beats", 32),
            ("--major-phrase-multiple", "4", "major_phrase_multiple", 4),
            ("--sample-rate", "44100", "sample_rate", 44100),
            ("--hop-length", "1024", "hop_length", 1024),
            ("--window-beats", "8.0", "window_beats", 8.0),
            ("--mfcc-count", "20", "mfcc_count", 20),
            ("--energy-threshold", "6.0", "energy_change_threshold_db", 6.0),
            ("--timbre-threshold", "25.0", "timbre_change_distance_threshold", 25.0),
            ("--max-cues", "5", "max_cues", 5),
            (
                "--relative-confidence-threshold",
                "0.5",
                "relative_confidence_threshold",
                0.5,
            ),
        ],
    )
    def test_every_tuning_flag_maps_to_its_config_field(
        self, flag, value, field_name, expected
    ):
        args = cli.build_parser().parse_args(
            ["track.mp3", "--nml", "collection.nml", flag, value]
        )
        config = cli.build_config_from_args(args)
        assert getattr(config, field_name) == expected

    def test_stems_dir_defaults_to_none(self):
        args = cli.build_parser().parse_args(["track.mp3", "--nml", "collection.nml"])
        config = cli.build_config_from_args(args)
        assert config.stems_dir is None

    def test_stems_dir_flag_overrides_config_field(self):
        args = cli.build_parser().parse_args(
            [
                "track.mp3",
                "--nml",
                "collection.nml",
                "--stems-dir",
                "D:/CustomStems",
            ]
        )
        config = cli.build_config_from_args(args)
        assert config.stems_dir == str(Path("D:/CustomStems"))

    def test_verify_defaults_to_smart(self):
        args = cli.build_parser().parse_args(["track.mp3", "--nml", "collection.nml"])
        config = cli.build_config_from_args(args)
        assert config.verify == "smart"

    def test_no_stems_defaults_to_false_and_flag_enables_it(self):
        base_args = cli.build_parser().parse_args(["track.mp3", "--nml", "collection.nml"])
        assert cli.build_config_from_args(base_args).no_stems is False

        stemless_args = cli.build_parser().parse_args(
            ["track.mp3", "--nml", "collection.nml", "--no-stems"]
        )
        assert cli.build_config_from_args(stemless_args).no_stems is True

    def test_verify_flag_overrides_config_field(self):
        args = cli.build_parser().parse_args(
            ["track.mp3", "--nml", "collection.nml", "--verify", "smart"]
        )
        config = cli.build_config_from_args(args)
        assert config.verify == "smart"

    def test_verify_rejects_invalid_choice(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(
                ["track.mp3", "--nml", "collection.nml", "--verify", "bogus"]
            )

    def test_main_passes_overridden_config_through_to_pipeline(
        self, tmp_path, monkeypatch
    ):
        captured_kwargs = {}

        def fake_run_pipeline(**kwargs):
            captured_kwargs.update(kwargs)
            return _fake_result()

        monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML/>", encoding="utf-8")

        cli.main(
            [
                "track.mp3",
                "--nml",
                str(nml_path),
                "--phrase-beats",
                "32",
                "--energy-threshold",
                "7.5",
            ]
        )

        assert captured_kwargs["config"].phrase_beats == 32
        assert captured_kwargs["config"].energy_change_threshold_db == 7.5
        # Unspecified fields still default.
        assert captured_kwargs["config"].max_cues == AppConfig().max_cues


class TestGetLibrary:
    def test_reports_missing_collection_as_structured_not_found_error(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_resolve_nml_path", lambda _explicit_nml: None)

        assert cli.main(["--get-library"]) == 1
        assert json.loads(capsys.readouterr().out.strip()) == {
            "error": "not_found",
            "message": "No collection.nml found.",
        }

    def test_prints_compact_relational_json_creates_backup_and_exits_zero(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        shutil.copy2(SAMPLE_COLLECTION, nml_path)
        original_bytes = nml_path.read_bytes()
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--nml", str(nml_path), "--get-library"])

        assert exc_info.value.code == 0
        backups = list((tmp_path / "CueGrid Backups").glob("collection.nml.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == original_bytes
        stdout = capsys.readouterr().out.strip()
        payload = json.loads(stdout)
        assert set(payload) == {"collection", "playlists"}
        assert len(payload["collection"]) == 2
        assert payload["playlists"][0]["kind"] == "folder"
        assert "\n" not in stdout

    def test_reports_duplicate_location_error_schema(self, tmp_path, capsys):
        nml_path = tmp_path / "duplicate.nml"
        nml_path.write_text(
            """<NML><COLLECTION>
<ENTRY TITLE="A" ARTIST="Artist"><LOCATION VOLUME="C:" DIR="/:Music/:" FILE="same.flac" /></ENTRY>
<ENTRY TITLE="B" ARTIST="Artist"><LOCATION VOLUME="C:" DIR="/:Music/:" FILE="same.flac" /></ENTRY>
</COLLECTION></NML>""",
            encoding="utf-8",
        )

        assert cli.main(["--nml", str(nml_path), "--get-library"]) == 1
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["error"] == "parse_error"
        assert "same.flac" in payload["message"]

    def test_reports_malformed_nml_as_structured_parse_error(self, tmp_path, capsys):
        nml_path = tmp_path / "collection.nml"
        nml_path.write_text("<NML><COLLECTION>", encoding="utf-8")

        assert cli.main(["--nml", str(nml_path), "--get-library"]) == 1
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["error"] == "parse_error"
        assert payload["message"]
