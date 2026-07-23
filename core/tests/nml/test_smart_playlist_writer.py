"""Tests for static Traktor playlist construction and injection."""

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from cuegrid.nml.parser import NmlParser
from cuegrid.nml.writer import NmlWriter, build_playlist_node, generate_playlist_uuid


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_COLLECTION = FIXTURES_DIR / "sample_collection.nml"


def test_generated_playlist_uuid_is_lowercase_32_character_hex():
    playlist_uuid = generate_playlist_uuid()
    assert re.fullmatch(r"[0-9a-f]{32}", playlist_uuid)


def test_build_playlist_node_has_exact_traktor_structure():
    keys = ["C:/:Music/:One.flac", "C:/:Music/:Two.mp3"]
    node = build_playlist_node("Warmup", keys, playlist_uuid="a" * 32)

    assert node.tag == "NODE"
    assert node.attrib == {"TYPE": "PLAYLIST", "NAME": "Warmup"}
    playlist = node.find("PLAYLIST")
    assert playlist is not None
    assert playlist.attrib == {"ENTRIES": "2", "TYPE": "LIST", "UUID": "a" * 32}
    assert [entry.find("PRIMARYKEY").attrib for entry in playlist.findall("ENTRY")] == [
        {"TYPE": "TRACK", "KEY": key} for key in keys
    ]


def test_writer_injects_playlist_into_root_subnodes_and_replaces_name(tmp_path):
    nml_path = tmp_path / "collection.nml"
    shutil.copy2(SAMPLE_COLLECTION, nml_path)
    parser = NmlParser(nml_path)
    entries = list(parser.tree.getroot().iterfind("./COLLECTION/ENTRY"))

    writer = NmlWriter(parser)
    first_uuid = writer.write_smart_playlist("Smart Techno", entries[:1])
    second_uuid = writer.write_smart_playlist("Smart Techno", entries)

    assert re.fullmatch(r"[0-9a-f]{32}", first_uuid)
    assert re.fullmatch(r"[0-9a-f]{32}", second_uuid)
    assert first_uuid != second_uuid

    root = ET.parse(nml_path).getroot()
    subnodes = root.find("./PLAYLISTS/NODE[@TYPE='FOLDER'][@NAME='$ROOT']/SUBNODES")
    assert subnodes is not None
    compiled = [
        node for node in subnodes.findall("NODE")
        if node.get("TYPE") == "PLAYLIST" and node.get("NAME") == "Smart Techno"
    ]
    assert len(compiled) == 1
    playlist = compiled[0].find("PLAYLIST")
    assert playlist is not None
    assert playlist.get("ENTRIES") == str(len(entries))
    assert playlist.get("UUID") == second_uuid
    assert len(playlist.findall("ENTRY")) == len(entries)
    assert playlist.find("ENTRY/PRIMARYKEY").get("KEY") == (
        "C:/:Users/:ska_m/:Music/:Tidal/:Machinedrum - NO 1 KNEW.flac"
    )
    assert subnodes.get("COUNT") == str(len(subnodes.findall("NODE")))
