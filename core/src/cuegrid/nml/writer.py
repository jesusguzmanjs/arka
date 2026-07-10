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

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from cuegrid.nml.models import CuePoint
from cuegrid.nml.parser import NmlParser

# Matches Traktor's own declaration exactly (spec section 3.1's example),
# rather than Python's default single-quoted, standalone-less declaration.
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'


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
        """Copy the original file to ``<name>.nml.bak`` (spec section 3.4)."""
        backup_path = Path(str(self._parser.nml_path) + ".bak")
        if not backup_path.exists():
            shutil.copy2(self._parser.nml_path, backup_path)

    def _write_atomic(self) -> None:
        """Serialize the tree to a temp file, then atomically replace the original."""
        nml_path = self._parser.nml_path
        tmp_path = Path(str(nml_path) + ".tmp")

        with open(tmp_path, "wb") as f:
            f.write(_XML_DECLARATION)
            self._parser.tree.write(f, encoding="UTF-8", xml_declaration=False)

        tmp_path.replace(nml_path)
