"""Atomic NML writing.

Implements the ``nml.writer`` responsibility from ``.openspec/2-spec.md``
section 2.1, and the writer constraints in section 3.4: append new
``<CUE_V2 TYPE="0">`` HotCue elements to a matched ``<ENTRY>`` without ever
touching the existing ``<CUE_V2 TYPE="4">`` (``AutoGrid``) anchor or any
other existing children, back up the original file first, and serialize
numeric attributes with 6 decimal places to match Traktor's own
formatting.

This module never re-parses or re-derives cue math (spec section 2.1) --
it only serializes ``CuePoint``s it is handed, using the live ``Element``
tree exposed by ``nml.parser.NmlParser``.
"""

from __future__ import annotations

import math
import platform
import shutil
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
import copy
import re
import uuid
from pathlib import Path

from cuegrid.nml.models import CuePoint
from cuegrid.nml.parser import (
    NmlParser,
    TRAKTOR_MUSICAL_KEY_TO_OPEN_KEY,
    TrackNotFoundError,
)

# Matches Traktor's own declaration exactly (spec section 3.1's example),
# rather than Python's default single-quoted, standalone-less declaration.
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
_MIN_BPM = 50.0
_MAX_BPM = 200.0
_PLAYLIST_UUID_RE = re.compile(r"^[0-9a-f]{32}$")
_REMIX_PAD_ID_RE = re.compile(r"^([A-D])(\d+)$")
_UNSAFE_REMIX_DIRECTORY_CHARS_RE = re.compile(r'[<>:"/\\\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_DIRECTORY_NAMES = {
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
    "LPT6", "LPT7", "LPT8", "LPT9",
}


def generate_playlist_uuid() -> str:
    """Return a Traktor-compatible, lowercase 32-hex-character UUID."""
    return uuid.uuid4().hex


def build_playlist_node(
    name: str, track_keys: list[str], *, playlist_uuid: str | None = None
) -> ET.Element:
    """Build one standard Traktor ``NODE TYPE=PLAYLIST`` XML element.

    ``track_keys`` must already be Traktor ``PRIMARYKEY`` path values, not
    normalized filesystem paths. Keeping construction independent of a parser
    makes the exact XML shape directly unit-testable.
    """
    playlist_name = name.strip()
    if not playlist_name:
        raise ValueError("playlist name must be non-empty")
    if any(not isinstance(key, str) or not key for key in track_keys):
        raise ValueError("playlist track keys must be non-empty strings")

    playlist_uuid = playlist_uuid or generate_playlist_uuid()
    if not _PLAYLIST_UUID_RE.fullmatch(playlist_uuid):
        raise ValueError("playlist UUID must be 32 lowercase hexadecimal characters")

    node = ET.Element("NODE", TYPE="PLAYLIST", NAME=playlist_name)
    playlist = ET.SubElement(
        node,
        "PLAYLIST",
        ENTRIES=str(len(track_keys)),
        TYPE="LIST",
        UUID=playlist_uuid,
    )
    for key in track_keys:
        entry = ET.SubElement(playlist, "ENTRY")
        ET.SubElement(entry, "PRIMARYKEY", TYPE="TRACK", KEY=key)
    return node


class HotcueSlotConflictError(Exception):
    """Raised if a CuePoint's HOTCUE slot collides with an existing one.

    This should never happen if ``core.mapping`` is used correctly (it is
    responsible for picking free slots -- spec section 3.4), but the
    writer double-checks defensively rather than silently corrupting a
    HotCue pad's contents in Traktor.
    """


class HotcueNotFoundError(Exception):
    """Raised when a requested standard HotCue is absent from an ENTRY."""


class NmlWriter:
    """Mutates matched ``ENTRY`` elements and writes the NML back atomically."""

    def __init__(self, parser: NmlParser) -> None:
        self._parser = parser

    @staticmethod
    def _path_to_nml_location(filepath: str) -> tuple[str, str, str]:
        """Convert an absolute OS path to Traktor's VOLUME, DIR, FILE syntax."""
        normalized_filepath = filepath[4:] if filepath.startswith("\\\\?\\") else filepath
        p = Path(normalized_filepath).expanduser().resolve()
        file_ = p.name

        if platform.system() == "Windows":
            volume = p.drive
            dir_parts = p.parts[1:-1]
        else:
            if len(p.parts) > 2 and p.parts[1] == "Volumes":
                volume = p.parts[2]
                dir_parts = p.parts[3:-1]
            else:
                volume = "Macintosh HD"
                dir_parts = p.parts[1:-1]

        dir_str = "/:" + "/:".join(dir_parts) + "/:" if dir_parts else "/:"
        return volume, dir_str, file_

    def write_remix_set(self, payload: dict) -> None:
        """Copy active pad audio and append a native root-level Remix ``SET``."""
        if not isinstance(payload, dict):
            raise ValueError("Remix Set payload must be a JSON object")

        title = payload.get("title", "New Remix Set")
        if not isinstance(title, str):
            raise ValueError("Remix Set title must be a string")
        title = title.strip() or "New Remix Set"
        columns = payload.get("columns", [])
        pads = payload.get("pads", [])
        if not isinstance(columns, list) or not all(isinstance(column, dict) for column in columns):
            raise ValueError("Remix Set columns must be an array of objects")
        if not isinstance(pads, list) or not all(isinstance(pad, dict) for pad in pads):
            raise ValueError("Remix Set pads must be an array of objects")

        active_pads = self._copy_remix_pad_audio(title, pads)
        snapshot = copy.deepcopy(self._parser.tree)
        try:
            root = self._parser.tree.getroot()
            collection_el = root.find("COLLECTION")
            if collection_el is None:
                raise ValueError("No COLLECTION node found in NML")

            # The backup intentionally happens immediately before the first
            # NML mutation. Failed source-file copies never touch the NML.
            self._backup_if_needed()

            default_vol_id = next(
                (
                    location.get("VOLUMEID")
                    for location in collection_el.findall(".//LOCATION")
                    if location.get("VOLUMEID")
                ),
                "",
            )
            sets_el = root.find("SETS")
            if sets_el is None:
                sets_el = ET.SubElement(root, "SETS", ENTRIES="0")
            set_el = ET.Element("SET")
            set_el.set("TITLE", title)
            set_el.set("QUANT_VAlUE", str(payload.get("quantize_value", 4)))
            set_el.set("QUANT_STATE", str(payload.get("quantize_state", 1)))

            now = datetime.now()
            import_date = f"{now.year}/{now.month}/{now.day}"
            virtual_set_filename = f"{now.strftime('%Yy%mm%dd_%Hh%Mm%Ss')}000000.set"
            virtual_set_path = Path(self._parser.nml_path).parent / virtual_set_filename
            set_volume, set_directory, _ = self._path_to_nml_location(str(virtual_set_path))
            location_el = ET.SubElement(set_el, "LOCATION")
            location_el.set("DIR", set_directory)
            location_el.set("FILE", virtual_set_filename)
            location_el.set("VOLUME", set_volume)
            if default_vol_id:
                location_el.set("VOLUMEID", default_vol_id)

            mod_info_el = ET.SubElement(set_el, "MODIFICATION_INFO")
            mod_info_el.set("AUTHOR_TYPE", "importer")

            info_el = ET.SubElement(set_el, "INFO")
            info_el.set("IMPORT_DATE", import_date)

            tempo_el = ET.SubElement(set_el, "TEMPO")
            tempo_el.set("BPM", f"{payload.get('bpm', 120.0):.6f}")
            for col_idx in range(4):
                slot_el = ET.SubElement(set_el, "SLOT")
                col_data = (
                    columns[col_idx]
                    if col_idx < len(columns)
                    else {"keylock": 1, "punchmode": 0}
                )
                slot_el.set("KEYLOCK", str(col_data.get("keylock", 1)))
                slot_el.set("PUNCHMODE", str(col_data.get("punchmode", 0)))
                slot_el.set("FXENABLE", "1")

                col_letter = chr(65 + col_idx)
                column_pads = active_pads.get(col_letter, [])
                slot_el.set("ACTIVE_CELL_INDEX", "0" if column_pads else "-1")
                for row_index, pad_data, destination in column_pads:
                    cell_el = ET.SubElement(slot_el, "CELL")
                    cell_el.set("INDEX", str(row_index))
                    cell_el.set("CELLNAME", str(pad_data.get("name", pad_data["id"])))
                    cell_el.set("COLOR", str(pad_data.get("color_id", 1)))
                    cell_el.set("SYNC", str(pad_data.get("sync", 1)))
                    cell_el.set("REVERSE", str(pad_data.get("reverse", 0)))
                    cell_el.set("MODE", str(pad_data.get("mode", 0)))
                    cell_el.set("TYPE", str(pad_data.get("type", 1)))
                    cell_el.set("SPEED", "1.000000")
                    cell_el.set("TRANSPOSE", f"{pad_data.get('transpose', 0):.6f}")
                    cell_el.set("OFFSET", "0.000000")
                    cell_el.set("NUDGE", "0.000000")
                    cell_el.set("GAIN", f"{pad_data.get('gain', 0.5):.6f}")
                    cell_el.set("START_MARKER", f"{pad_data.get('start_ms', 0.0):.6f}")
                    cell_el.set("END_MARKER", f"{pad_data.get('end_ms', 0.0):.6f}")
                    cell_el.set("BPM", f"{pad_data.get('bpm', 120.0):.6f}")

                    sample_volume, sample_directory, file_ = self._path_to_nml_location(str(destination))
                    cell_el.set("DIR", sample_directory)
                    cell_el.set("FILE", file_)
                    cell_el.set("VOLUME", sample_volume)

            seconds_since_midnight = now.hour * 3600 + now.minute * 60 + now.second
            open_key_to_traktor = {
                open_key.lower(): traktor_key
                for traktor_key, open_key in TRAKTOR_MUSICAL_KEY_TO_OPEN_KEY.items()
            }
            for column_pads in active_pads.values():
                for _row_index, pad_data, destination in column_pads:
                    try:
                        self._parser._find_matching_elements(destination, None, None)
                    except TrackNotFoundError:
                        pass
                    else:
                        continue

                    sample_entry = ET.SubElement(collection_el, "ENTRY")
                    sample_entry.set("MODIFIED_DATE", import_date)
                    sample_entry.set("MODIFIED_TIME", str(seconds_since_midnight))
                    sample_entry.set("LOCK", "1")
                    sample_entry.set(
                        "LOCK_MODIFICATION_TIME", now.strftime("%Y-%m-%dT%H:%M:%S")
                    )
                    sample_entry.set("TITLE", str(pad_data.get("name", pad_data.get("id", ""))))
                    sample_location = ET.SubElement(sample_entry, "LOCATION")
                    sample_volume, sample_directory, sample_file = self._path_to_nml_location(
                        str(destination)
                    )
                    sample_location.set("DIR", sample_directory)
                    sample_location.set("FILE", sample_file)
                    sample_location.set("VOLUME", sample_volume)
                    if default_vol_id:
                        sample_location.set("VOLUMEID", default_vol_id)
                    modification_info = ET.SubElement(sample_entry, "MODIFICATION_INFO")
                    modification_info.set("AUTHOR_TYPE", "importer")
                    sample_info = ET.SubElement(sample_entry, "INFO")
                    sample_info.set("IMPORT_DATE", import_date)
                    sample_info.set("FLAGS", "28")
                    sample_info.set("COMMENT", f"Arka: {title}")
                    duration_sec = pad_data.get("duration_ms", 0.0) / 1000.0
                    if duration_sec > 0:
                        sample_info.set("PLAYTIME", str(int(duration_sec)))
                        sample_info.set("PLAYTIME_FLOAT", f"{duration_sec:.6f}")
                    sample_info.set("FILESIZE", str(destination.stat().st_size))
                    bpm_str = f"{pad_data.get('bpm', 120.0):.6f}"
                    sample_tempo = ET.SubElement(sample_entry, "TEMPO")
                    sample_tempo.set("BPM", bpm_str)
                    sample_tempo.set("BPM_QUALITY", "100.000000")
                    pad_key = str(pad_data.get("key", "")).strip().lower()
                    if pad_key in open_key_to_traktor:
                        musical_key = ET.SubElement(sample_entry, "MUSICAL_KEY")
                        musical_key.set("VALUE", str(open_key_to_traktor[pad_key]))
                    sample_grid_cue = ET.SubElement(sample_entry, "CUE_V2")
                    sample_grid_cue.set("NAME", "AutoGrid")
                    sample_grid_cue.set("DISPL_ORDER", "0")
                    sample_grid_cue.set("TYPE", "4")
                    sample_grid_cue.set("START", "0.000000")
                    sample_grid_cue.set("LEN", "0.000000")
                    sample_grid_cue.set("REPEATS", "-1")
                    sample_grid_cue.set("HOTCUE", "-1")
                    sample_grid = ET.SubElement(sample_grid_cue, "GRID")
                    sample_grid.set("BPM", bpm_str)

            existing_set = next(
                (set_node for set_node in sets_el.findall("SET") if set_node.get("TITLE") == title),
                None,
            )
            if existing_set is None:
                sets_el.append(set_el)
            else:
                insertion_index = list(sets_el).index(existing_set)
                sets_el.remove(existing_set)
                sets_el.insert(insertion_index, set_el)
            sets_el.set("ENTRIES", str(len(sets_el.findall("SET"))))
            collection_el.set("ENTRIES", str(len(collection_el.findall("ENTRY"))))

            self._write_atomic()
        except Exception:
            self._parser.restore_tree(snapshot)
            raise

    @staticmethod
    def _safe_remix_set_directory_name(title: str) -> str:
        """Return a portable, non-empty directory name derived from a set title."""
        sanitized = _UNSAFE_REMIX_DIRECTORY_CHARS_RE.sub("_", title).strip(". ")
        if not sanitized:
            return "New Remix Set"
        return f"_{sanitized}" if sanitized.upper() in _WINDOWS_RESERVED_DIRECTORY_NAMES else sanitized

    def _copy_remix_pad_audio(
        self, title: str, pads: list[dict]
    ) -> dict[str, list[tuple[int, dict, Path]]]:
        """Copy active pad audio into its durable per-Set Traktor directory."""
        target_directory = (
            Path.home()
            / "Music"
            / "Traktor"
            / "Samples"
            / "Arka"
            / self._safe_remix_set_directory_name(title)
        )
        active_pads: dict[str, list[tuple[int, dict, Path]]] = {
            column: [] for column in "ABCD"
        }
        seen_pad_ids: set[str] = set()
        seen_cell_indices: set[tuple[str, int]] = set()

        for pad_data in pads:
            path = pad_data.get("path")
            if not path:
                continue
            pad_id = pad_data.get("id")
            if not isinstance(pad_id, str):
                raise ValueError("active Remix Set pad requires a string id")
            pad_match = _REMIX_PAD_ID_RE.fullmatch(pad_id)
            if pad_match is None:
                raise ValueError(f"invalid Remix Set pad id: {pad_id}")
            if pad_id in seen_pad_ids:
                raise ValueError(f"duplicate Remix Set pad id: {pad_id}")
            column, row = pad_match.groups()
            row_index = int(row) - 1
            if row_index < 0:
                raise ValueError(f"Remix Set pad id must start at row 1: {pad_id}")
            if (column, row_index) in seen_cell_indices:
                raise ValueError(f"duplicate Remix Set cell index: {pad_id}")
            if not isinstance(path, str):
                raise ValueError(f"Remix Set pad {pad_id} path must be a string")

            normalized_path = path[4:] if path.startswith("\\\\?\\") else path
            source = Path(normalized_path).expanduser().resolve()
            try:
                self._parser._find_matching_elements(normalized_path, None, None)
            except TrackNotFoundError:
                if not source.is_file():
                    raise FileNotFoundError(f"Remix Set pad source does not exist: {source}")
                target_directory.mkdir(parents=True, exist_ok=True)
                destination = target_directory / f"{pad_id}_{source.name}"
                if source != destination.resolve():
                    shutil.copy2(source, destination)
            else:
                destination = source

            active_pads[column].append((row_index, pad_data, destination))
            seen_pad_ids.add(pad_id)
            seen_cell_indices.add((column, row_index))

        for column_pads in active_pads.values():
            column_pads.sort(key=lambda item: item[0])
        return active_pads

    def write_cues(
        self,
        track_path: str | Path,
        cues: list[CuePoint],
        title: str | None = None,
        artist: str | None = None,
        clear_existing: bool = False,
    ) -> None:
        """Append ``cues`` as new ``<CUE_V2>`` elements on the matched ``ENTRY``.

        Implements spec section 3.4 in full:

        - Locates the ``ENTRY`` via the same matching logic as
          ``NmlParser.find_entry`` (section 7) -- never re-derived here.
        - Optionally clears existing standard HotCues before appending
          (``clear_existing=True``), leaving Grid/Load markers untouched.
        - Only *appends* new ``TYPE=0`` ``<CUE_V2>`` children; the existing
          ``AutoGrid`` (``TYPE=4``) cue and every other existing child is
          left byte-for-byte untouched.
        - Refuses to write a cue whose ``HOTCUE`` slot collides with one
          already present on the entry.
        - Backs up the original file to ``<name>.nml.bak`` first, unless a
          backup for this run already exists.
        - Serializes every numeric attribute with 6 decimal places
          (``f"{value:.6f}"``) to match Traktor's own formatting.
        - Writes atomically: the new content is written to a temporary
          file first, then swapped into place with ``Path.replace``.

        Args:
            track_path: The audio file path identifying which ``ENTRY``
                to update (same value passed to ``NmlParser.find_entry``).
            cues: The new ``CuePoint``s to append. If empty, the file is
                not touched at all -- no backup, no write.
            title: Optional disambiguation filter, forwarded to
                ``NmlParser.find_entry_element`` (spec section 7.3, step
                6) -- must match whatever was used to resolve the
                ``TrackEntry`` this ``cues`` list was computed from.
            artist: Optional disambiguation filter, same purpose.
            clear_existing: If ``True``, remove all existing standard
                HotCues (``TYPE="0"``) from the entry before appending.
                Grid markers (``TYPE="4"``) and Load markers (``TYPE="3"``)
                are never removed. Defaults to ``False``.

        Raises:
            TrackNotFoundError: if no ``ENTRY`` matches ``track_path``.
            AmbiguousTrackError: if more than one ``ENTRY`` matches, even
                after applying any ``title``/``artist`` filters given.
            HotcueSlotConflictError: if a cue's ``HOTCUE`` slot is already
                occupied on the matched ``ENTRY``.
        """
        if not cues:
            return

        entry_el = self._parser.find_entry_element(track_path, title, artist)
        self.write_cues_to_element(entry_el, cues, clear_existing=clear_existing)
        self._backup_if_needed()
        self._write_atomic()

    def delete_cue(
        self,
        track_path: str | Path,
        hotcue_index: int,
        title: str | None = None,
        artist: str | None = None,
    ) -> None:
        """Remove one standard HotCue and atomically persist the NML.

        Only a ``TYPE="0"`` cue with the requested zero-based ``HOTCUE``
        index is eligible. The tree is not written when the track or cue
        cannot be found.
        """
        if not 0 <= hotcue_index <= 7:
            raise ValueError(f"HOTCUE index must be between 0 and 7, got {hotcue_index}")

        entry_el = self._parser.find_entry_element(track_path, title, artist)
        cue_el = next(
            (
                element
                for element in entry_el.findall("CUE_V2")
                if element.get("TYPE") == "0"
                and element.get("HOTCUE") == str(hotcue_index)
            ),
            None,
        )
        if cue_el is None:
            raise HotcueNotFoundError(
                f'No standard HotCue with HOTCUE="{hotcue_index}" found for '
                f"track {track_path!s}"
            )

        entry_el.remove(cue_el)
        try:
            self._backup_if_needed()
            self._write_atomic()
        except Exception:
            # Keep the in-memory tree consistent for callers that catch an
            # I/O failure and continue using the parser instance.
            entry_el.append(cue_el)
            raise

    def update_track_hotcues(
        self,
        track_path: str | Path,
        cues_list: list[dict],
        title: str | None = None,
        artist: str | None = None,
        grid_anchor_ms: float | None = None,
        bpm: float | None = None,
    ) -> None:
        """Update standard HotCues, one Grid anchor, and/or BPM atomically.

        Designed for frontend UI sync (drag & drop adjustments). Modifies the
        START attribute of existing CUE_V2 elements to preserve user-defined
        names, rather than destroying and recreating the nodes. When
        ``grid_anchor_ms`` is supplied, the target must have exactly one Grid
        marker; Flex Grid entries are rejected before any XML is mutated.
        When ``bpm`` is supplied, the target must contain a direct ``TEMPO``
        child and the value must be finite and within the inclusive range
        ``[50, 200]``.

        NML ``START`` values in this project are milliseconds, so no unit
        conversion is performed before six-decimal formatting.
        """
        entry_el = self._parser.find_entry_element(track_path, title, artist)

        normalized_cues = self._validate_manual_cues(cues_list)
        normalized_bpm = self._validate_bpm(bpm)
        grid_marker: ET.Element | None = None
        if grid_anchor_ms is not None:
            if not math.isfinite(grid_anchor_ms) or grid_anchor_ms < 0:
                raise ValueError("grid anchor must be a finite value greater than or equal to zero")
            grid_markers = [
                element
                for element in entry_el.findall("CUE_V2")
                if element.get("TYPE") == "4"
            ]
            if len(grid_markers) != 1:
                if len(grid_markers) > 1:
                    raise ValueError("cannot update grid anchor for a Flex Grid track")
                raise ValueError("cannot update grid anchor: no Grid marker found")
            grid_marker = grid_markers[0]

        original_starts: dict[ET.Element, str | None] = {}
        created_cues: list[ET.Element] = []
        grid_start = grid_marker.get("START") if grid_marker is not None else None
        tempo_el = entry_el.find("TEMPO") if normalized_bpm is not None else None
        if normalized_bpm is not None and tempo_el is None:
            raise ValueError("cannot update BPM: no TEMPO element found")
        original_bpm = tempo_el.get("BPM") if tempo_el is not None else None
        try:
            for hotcue, start_ms in normalized_cues:
                hotcue_str = str(hotcue)
                cue_el = next(
                    (
                        el
                        for el in entry_el.findall("CUE_V2")
                        if el.get("TYPE") == "0" and el.get("HOTCUE") == hotcue_str
                    ),
                    None,
                )
                formatted_time = f"{start_ms:.6f}"

                if cue_el is not None:
                    original_starts[cue_el] = cue_el.get("START")
                    cue_el.set("START", formatted_time)
                else:
                    cue_el = ET.SubElement(entry_el, "CUE_V2")
                    created_cues.append(cue_el)
                    cue_el.set("NAME", f"Cue {hotcue + 1}")
                    cue_el.set("DISPL_ORDER", "0")
                    cue_el.set("TYPE", "0")
                    cue_el.set("START", formatted_time)
                    cue_el.set("LEN", "0.000000")
                    cue_el.set("REPEATS", "-1")
                    cue_el.set("HOTCUE", hotcue_str)

            if grid_marker is not None:
                grid_marker.set("START", f"{grid_anchor_ms:.6f}")
            if tempo_el is not None and normalized_bpm is not None:
                tempo_el.set("BPM", f"{normalized_bpm:.6f}")

            self._backup_if_needed()
            self._write_atomic()
        except Exception:
            for cue_el, original_start in original_starts.items():
                if original_start is None:
                    cue_el.attrib.pop("START", None)
                else:
                    cue_el.set("START", original_start)
            for cue_el in created_cues:
                entry_el.remove(cue_el)
            if grid_marker is not None:
                if grid_start is None:
                    grid_marker.attrib.pop("START", None)
                else:
                    grid_marker.set("START", grid_start)
            if tempo_el is not None:
                if original_bpm is None:
                    tempo_el.attrib.pop("BPM", None)
                else:
                    tempo_el.set("BPM", original_bpm)
            raise

    def write_metadata_batch(
        self, updates: list[tuple[ET.Element, dict[str, str | int | None]]]
    ) -> None:
        """Apply metadata patches to live entries and atomically write once.

        All payload validation and track resolution are performed by the
        caller before this method is invoked. A deep snapshot keeps the
        parser usable if the one batch write fails after the XML tree has
        been mutated.
        """
        if not updates:
            return

        snapshot = copy.deepcopy(self._parser.tree)
        try:
            for entry_el, fields in updates:
                self.apply_metadata_to_element(entry_el, fields)
            self._backup_if_needed()
            self._write_atomic()
        except Exception:
            self._parser.restore_tree(snapshot)
            raise

    def write_batch_save(
        self,
        updates: list[
            tuple[
                ET.Element,
                list[dict] | None,
                float | None,
                float | None,
                dict[str, str | int | None] | None,
            ]
        ],
        playlist_updates: list[tuple[ET.Element, str, str | None, list[str] | None]] | None = None,
    ) -> None:
        """Apply final GUI track and playlist state and persist exactly once.

        Callers resolve entries and validate the full payload before invoking
        this method. A tree snapshot makes the all-or-nothing NML transaction
        recoverable when serialization or replacement fails.
        """
        snapshot = copy.deepcopy(self._parser.tree)
        try:
            for entry_el, cues, grid_anchor_ms, bpm, metadata in updates:
                if cues is not None:
                    self._replace_standard_hotcues(
                        entry_el, self._validate_manual_cues(cues)
                    )
                if grid_anchor_ms is not None:
                    if not math.isfinite(grid_anchor_ms) or grid_anchor_ms < 0:
                        raise ValueError("grid anchor must be finite and non-negative")
                    grids = [cue for cue in entry_el.findall("CUE_V2") if cue.get("TYPE") == "4"]
                    if len(grids) != 1:
                        raise ValueError("cannot update grid anchor without exactly one Grid marker")
                    grids[0].set("START", f"{grid_anchor_ms:.6f}")
                normalized_bpm = self._validate_bpm(bpm)
                if normalized_bpm is not None:
                    tempo = entry_el.find("TEMPO")
                    if tempo is None:
                        raise ValueError("cannot update BPM: no TEMPO element found")
                    tempo.set("BPM", f"{normalized_bpm:.6f}")
                if metadata is not None:
                    self.apply_metadata_to_element(entry_el, metadata)
            for playlist_node, action, name, entry_keys in playlist_updates or []:
                if action == "delete":
                    parent = self._find_parent(playlist_node)
                    if parent is None:
                        raise ValueError("cannot delete playlist without a parent node")
                    parent.remove(playlist_node)
                    if parent.tag == "SUBNODES":
                        parent.set("COUNT", str(sum(1 for child in parent if child.tag == "NODE")))
                    continue
                if action != "update" or name is None or entry_keys is None:
                    raise ValueError("invalid playlist mutation")
                playlist_el = playlist_node.find("PLAYLIST")
                if playlist_el is None:
                    raise ValueError("playlist node has no PLAYLIST element")
                playlist_node.set("NAME", name)
                for entry in list(playlist_el.findall("ENTRY")):
                    playlist_el.remove(entry)
                for key in entry_keys:
                    entry = ET.SubElement(playlist_el, "ENTRY")
                    ET.SubElement(entry, "PRIMARYKEY", TYPE="TRACK", KEY=key)
                playlist_el.set("ENTRIES", str(len(entry_keys)))
            self._backup_if_needed()
            self._write_atomic()
        except Exception:
            self._parser.restore_tree(snapshot)
            raise

    def _find_parent(self, child: ET.Element) -> ET.Element | None:
        """Return the live tree parent for a direct child element."""
        for parent in self._parser.tree.getroot().iter():
            if child in parent:
                return parent
        return None

    @staticmethod
    def _replace_standard_hotcues(
        entry_el: ET.Element, cues: list[tuple[int, float]]
    ) -> None:
        """Replace only standard HotCues, preserving grid/load markers."""
        NmlWriter._clear_hotcues(entry_el)
        for hotcue, start_ms in cues:
            cue_el = ET.SubElement(entry_el, "CUE_V2")
            cue_el.set("NAME", f"Cue {hotcue + 1}")
            cue_el.set("DISPL_ORDER", "0")
            cue_el.set("TYPE", "0")
            cue_el.set("START", f"{start_ms:.6f}")
            cue_el.set("LEN", "0.000000")
            cue_el.set("REPEATS", "-1")
            cue_el.set("HOTCUE", str(hotcue))

    def write_smart_playlist(self, name: str, matched_entries: list[ET.Element]) -> str:
        """Compile matching collection entries into a static playlist and persist it.

        The playlist is placed directly in the ``$ROOT`` folder's ``SUBNODES``
        container. An existing same-name playlist is replaced in place, so the
        writer never creates duplicate names during a refresh.
        """
        track_keys = [self._entry_to_primary_key(entry) for entry in matched_entries]
        playlist_node = build_playlist_node(name, track_keys)
        playlist_uuid = playlist_node.find("PLAYLIST").get("UUID")
        snapshot = copy.deepcopy(self._parser.tree)
        try:
            subnodes = self._root_playlist_subnodes()
            same_name = [
                node
                for node in list(subnodes)
                if node.tag == "NODE"
                and node.get("TYPE") == "PLAYLIST"
                and node.get("NAME") == playlist_node.get("NAME")
            ]
            if same_name:
                insertion_index = list(subnodes).index(same_name[0])
                for node in same_name:
                    subnodes.remove(node)
                subnodes.insert(insertion_index, playlist_node)
            else:
                subnodes.append(playlist_node)
            subnodes.set("COUNT", str(sum(1 for node in subnodes if node.tag == "NODE")))
            self._backup_if_needed()
            self._write_atomic()
        except Exception:
            self._parser.restore_tree(snapshot)
            raise
        return playlist_uuid

    def write_static_playlist(self, name: str, entries: list[ET.Element]) -> str:
        """Persist an ordered regular playlist from validated collection entries.

        Reuses the Smart Playlist transaction because both produce the same
        standard Traktor playlist node, including its atomic write guarantees.
        Repeated elements are intentionally retained as repeated playlist rows.
        """
        return self.write_smart_playlist(name, entries)

    def _root_playlist_subnodes(self) -> ET.Element:
        root = self._parser.tree.getroot()
        playlists = root.find("PLAYLISTS")
        if playlists is None:
            playlists = ET.SubElement(root, "PLAYLISTS")
        root_folder = next(
            (
                node
                for node in playlists.findall("NODE")
                if node.get("TYPE") == "FOLDER" and node.get("NAME") == "$ROOT"
            ),
            None,
        )
        if root_folder is None:
            root_folder = ET.SubElement(playlists, "NODE", TYPE="FOLDER", NAME="$ROOT")
        subnodes = root_folder.find("SUBNODES")
        if subnodes is None:
            subnodes = ET.SubElement(root_folder, "SUBNODES", COUNT="0")
        return subnodes

    @staticmethod
    def _entry_to_primary_key(entry_el: ET.Element) -> str:
        location = entry_el.find("LOCATION")
        if location is None:
            raise ValueError("Smart Playlist entry has no LOCATION element")
        volume = location.get("VOLUME", "")
        directory = location.get("DIR", "")
        file_name = location.get("FILE", "")
        if not volume or not directory or not file_name:
            raise ValueError("Smart Playlist entry has incomplete LOCATION data")
        return f"{volume}{directory}{file_name}"

    @staticmethod
    def apply_metadata_to_element(
        entry_el: ET.Element, fields: dict[str, str | int | None]
    ) -> None:
        """Apply one validated partial metadata patch without writing XML."""
        entry_attributes = {"title": "TITLE", "artist": "ARTIST"}
        album_attributes = {"release": "TITLE"}
        info_attributes = {
            "remixer": "REMIXER",
            "producer": "PRODUCER",
            "genre": "GENRE",
            "label": "LABEL",
            "comment": "COMMENT",
            "comment2": "RATING",
            "lyrics": "KEY_LYRICS",
            "mix": "MIX",
        }

        album_el: ET.Element | None = None
        info_el: ET.Element | None = None
        for field, value in fields.items():
            if field in entry_attributes:
                NmlWriter._set_or_remove_attribute(
                    entry_el, entry_attributes[field], value
                )
            elif field in album_attributes:
                if album_el is None:
                    album_el = entry_el.find("ALBUM")
                    if album_el is None and value is not None:
                        album_el = ET.SubElement(entry_el, "ALBUM")
                if album_el is not None:
                    NmlWriter._set_or_remove_attribute(
                        album_el, album_attributes[field], value
                    )
            elif field in info_attributes or field == "rating":
                if info_el is None:
                    info_el = entry_el.find("INFO")
                    if info_el is None and value is not None:
                        info_el = ET.SubElement(entry_el, "INFO")
                if info_el is not None:
                    if field == "rating":
                        value = None if value is None else str(int(value) * 51)
                        NmlWriter._set_or_remove_attribute(info_el, "RANKING", value)
                    else:
                        NmlWriter._set_or_remove_attribute(
                            info_el, info_attributes[field], value
                        )
            else:
                raise ValueError(f"unsupported metadata field: {field}")

    @staticmethod
    def _set_or_remove_attribute(
        element: ET.Element, attribute: str, value: str | int | None
    ) -> None:
        if value is None:
            element.attrib.pop(attribute, None)
        else:
            element.set(attribute, str(value))

    @staticmethod
    def _validate_bpm(bpm: float | None) -> float | None:
        """Validate the optional manual BPM update before mutating XML."""
        if bpm is None:
            return None
        if not math.isfinite(bpm) or not _MIN_BPM <= bpm <= _MAX_BPM:
            raise ValueError("BPM must be a finite value between 50 and 200")
        return float(bpm)

    @staticmethod
    def _validate_manual_cues(cues_list: list[dict]) -> list[tuple[int, float]]:
        """Validate the complete manual-save payload before mutating XML."""
        if not isinstance(cues_list, list):
            raise ValueError("--update-cues must be a JSON array")

        normalized: list[tuple[int, float]] = []
        seen_hotcues: set[int] = set()
        for cue_data in cues_list:
            if not isinstance(cue_data, dict):
                raise ValueError("each manual cue must be an object")
            try:
                hotcue = int(cue_data["hotcue"])
                start_ms = float(cue_data["start_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("each manual cue requires numeric hotcue and start_ms") from exc
            if not 0 <= hotcue <= 7:
                raise ValueError(f"HOTCUE index must be between 0 and 7, got {hotcue}")
            if not math.isfinite(start_ms) or start_ms < 0:
                raise ValueError("manual cue start_ms must be finite and non-negative")
            if hotcue in seen_hotcues:
                raise ValueError(f"duplicate HOTCUE index in manual update: {hotcue}")
            seen_hotcues.add(hotcue)
            normalized.append((hotcue, start_ms))
        return normalized

    def write_cues_to_element(
        self, entry_el: ET.Element, cues: list[CuePoint], clear_existing: bool = False
    ) -> None:
        """Append ``cues`` as new ``<CUE_V2>`` elements to a given ``<ENTRY>`` element.

        This is the core primitive used by both ``write_cues`` (spec section 3.4)
        and batch processing (spec section 8.3) to avoid re-deriving the
        element by path in batch mode.

        When ``clear_existing`` is ``True``, existing standard HotCues
        (``TYPE="0"``) are removed before slot conflict checking and
        appending. Grid and Load markers (``TYPE="4"`` / ``TYPE="3"``)
        are never removed.

        Args:
            entry_el: The ``<ENTRY>`` element to append cues to (must be
                the live element from the parsed tree).
            cues: The new ``CuePoint``s to append.
            clear_existing: Remove existing HotCues first if ``True``.

        Raises:
            HotcueSlotConflictError: if a cue's ``HOTCUE`` slot is already
                occupied on the entry.
        """
        if clear_existing:
            self._clear_hotcues(entry_el)

        occupied_slots = self._occupied_hotcue_slots(entry_el)

        for cue in cues:
            if cue.hotcue != -1 and cue.hotcue in occupied_slots:
                raise HotcueSlotConflictError(
                    f"HOTCUE slot {cue.hotcue} is already occupied on this ENTRY; "
                    "core.mapping must assign a free slot before calling the writer"
                )
            self._append_cue(entry_el, cue)
            if cue.hotcue != -1:
                occupied_slots.add(cue.hotcue)

    @staticmethod
    def _clear_hotcues(entry_el: ET.Element) -> None:
        """Remove all standard HotCue ``<CUE_V2>`` elements (``TYPE="0"``)
        from the entry, leaving Grid (``TYPE="4"``), Load (``TYPE="3"``),
        and other marker types untouched.
        """
        to_remove = [el for el in entry_el.findall("CUE_V2") if el.get("TYPE") == "0"]
        for el in to_remove:
            entry_el.remove(el)

    @staticmethod
    def _occupied_hotcue_slots(entry_el: ET.Element) -> set[int]:
        occupied = set()
        for cue_el in entry_el.findall("CUE_V2"):
            hotcue = int(cue_el.get("HOTCUE", "-1"))
            if hotcue != -1:
                occupied.add(hotcue)
        return occupied

    @staticmethod
    def _append_cue(entry_el: ET.Element, cue: CuePoint) -> None:
        """Append one ``<CUE_V2>`` child, never touching existing children."""
        cue_el = ET.SubElement(entry_el, "CUE_V2")
        cue_el.set("NAME", cue.name)
        cue_el.set("DISPL_ORDER", str(cue.displ_order))
        cue_el.set("TYPE", str(int(cue.type)))
        cue_el.set("START", f"{cue.start_ms:.6f}")
        cue_el.set("LEN", f"{cue.len_ms:.6f}")
        cue_el.set("REPEATS", str(cue.repeats))
        cue_el.set("HOTCUE", str(cue.hotcue))

    def _backup_if_needed(self) -> None:
        """Create the daily backup and retain the five newest backups.

        A pre-existing backup for today is preserved, but retention still
        runs so an interrupted or older release cannot leave stale backups
        beyond the documented limit.
        """
        nml_path = Path(self._parser.nml_path)

        # 1. Definir y crear la subcarpeta "CueGrid Backups" junto al collection.nml
        backup_dir = nml_path.parent / "CueGrid Backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # 2. Generar el nombre del backup de hoy
        today_str = date.today().strftime("%Y%m%d")
        backup_file_name = f"{nml_path.name}.{today_str}.bak"
        backup_path = backup_dir / backup_file_name

        # Preserve the first collection state seen on a given day.
        if not backup_path.exists():
            shutil.copy2(nml_path, backup_path)

        # Retain only the five most recent daily backups for this collection.
        all_backups = sorted(backup_dir.glob(f"{nml_path.name}.*.bak"))

        for old_backup in all_backups[:-5]:
            old_backup.unlink(missing_ok=True)

    def _write_atomic(self) -> None:
        start_time = time.time()
        nml_path = self._parser.nml_path
        tmp_path = Path(str(nml_path) + ".tmp")

        with open(tmp_path, "wb") as f:
            f.write(_XML_DECLARATION)
            self._parser.tree.write(f, encoding="UTF-8", xml_declaration=False)

        tmp_path.replace(nml_path)
        import sys
        print(f"I/O Time: {time.time() - start_time:.4f} seconds", file=sys.stderr)
