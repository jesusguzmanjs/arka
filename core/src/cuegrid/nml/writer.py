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
import shutil
import time
import xml.etree.ElementTree as ET
import datetime
from pathlib import Path

from cuegrid.nml.models import CuePoint
from cuegrid.nml.parser import NmlParser

# Matches Traktor's own declaration exactly (spec section 3.1's example),
# rather than Python's default single-quoted, standalone-less declaration.
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
_MIN_BPM = 50.0
_MAX_BPM = 200.0


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
        """Crea un backup diario (máximo 5) en una subcarpeta dedicada."""
        nml_path = Path(self._parser.nml_path)

        # 1. Definir y crear la subcarpeta "CueGrid Backups" junto al collection.nml
        backup_dir = nml_path.parent / "CueGrid Backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # 2. Generar el nombre del backup de hoy
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        backup_file_name = f"{nml_path.name}.{today_str}.bak"
        backup_path = backup_dir / backup_file_name

        # 3. Si ya tenemos un backup de HOY, salimos inmediatamente.
        if backup_path.exists():
            return

        # 4. Si no existe, copiamos el NML actual a la nueva carpeta
        shutil.copy2(nml_path, backup_path)

        # 5. Limpieza: mantener solo los 5 backups más recientes en esa carpeta
        all_backups = sorted(backup_dir.glob(f"{nml_path.name}.*.bak"))

        if len(all_backups) > 5:
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
