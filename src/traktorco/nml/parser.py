"""Read-only NML parsing.

Implements the responsibilities described in ``.openspec/2-spec.md``
section 2.1 (``nml.parser``) and section 7 (``LOCATION`` matching and path
normalization): locate an ``<ENTRY>`` in a Traktor ``collection.nml`` by
matching its ``<LOCATION>`` against a user-supplied audio file path, then
extract ``TempoInfo``, the grid anchor, track duration, and any existing
``CuePoint``s into a ``TrackEntry``.

This module never mutates the parsed tree -- see ``nml/writer.py`` (not
yet implemented) for writing cues back to the file.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath, PureWindowsPath

from traktorco.nml.constants import CueType
from traktorco.nml.models import CuePoint, TempoInfo, TrackEntry

_WINDOWS_VOLUME_RE = re.compile(r"^[A-Za-z]:$")


class TrackNotFoundError(Exception):
    """Raised when no ``<ENTRY>`` in the collection matches the requested path."""


class AmbiguousTrackError(Exception):
    """Raised when more than one ``<ENTRY>`` matches the requested path."""


def nml_location_to_path(volume: str, dir_: str, file_: str) -> str:
    """Reconstruct a normalized, comparable path string from a ``LOCATION``.

    Implements ``.openspec/2-spec.md`` section 7.2. Returns a string with
    forward slashes and normalized casing (``casefold()``), NOT a resolved
    filesystem path -- the referenced volume may not be mounted on the
    machine running this tool.

    Args:
        volume: The ``LOCATION`` element's ``VOLUME`` attribute. A Windows
            drive letter (e.g. ``"C:"``) or a macOS volume name (e.g.
            ``"Macintosh HD"``).
        dir_: The ``LOCATION`` element's ``DIR`` attribute, e.g.
            ``"/:Users/:dj/:Music/:"``.
        file_: The ``LOCATION`` element's ``FILE`` attribute (bare filename).

    Returns:
        A normalized, comparable path string.
    """
    segments = [s for s in dir_.split("/:") if s]
    if _WINDOWS_VOLUME_RE.match(volume):
        raw = str(PureWindowsPath(volume + "\\", *segments, file_))
    else:
        # macOS-style volume name; best-effort only, not required to
        # resolve on a Windows machine running this tool.
        raw = str(PurePosixPath("/Volumes", volume, *segments, file_))
    return raw.replace("\\", "/").casefold()


class NmlParser:
    """Read-only parser for a Traktor ``collection.nml`` file."""

    def __init__(self, nml_path: str | Path) -> None:
        self.nml_path = Path(nml_path)
        self._tree = ET.parse(self.nml_path)
        self._root = self._tree.getroot()

    def find_entry(
        self,
        track_path: str | Path,
        title: str | None = None,
        artist: str | None = None,
    ) -> TrackEntry:
        """Locate the ``<ENTRY>`` matching ``track_path`` and extract it.

        Implements the matching strategy from ``.openspec/2-spec.md``
        section 7.3.

        Args:
            track_path: The user-supplied audio file path (need not exist
                on disk; only its normalized string form is compared
                against each ``<ENTRY>``'s ``LOCATION``).
            title: Optional disambiguation filter (case-insensitive exact
                match against ``ENTRY``'s ``TITLE``), used to narrow down
                an ambiguous ``LOCATION`` match (spec section 7.3, step 6).
            artist: Optional disambiguation filter (case-insensitive exact
                match against ``ENTRY``'s ``ARTIST``), same purpose.

        Returns:
            The matched ``TrackEntry``.

        Raises:
            TrackNotFoundError: if no ``<ENTRY>`` matches.
            AmbiguousTrackError: if more than one ``<ENTRY>`` matches, even
                after applying any ``title``/``artist`` filters given.
        """
        return self._entry_from_element(
            self._find_matching_elements(track_path, title, artist)[0]
        )

    def _find_matching_elements(
        self,
        track_path: str | Path,
        title: str | None = None,
        artist: str | None = None,
    ) -> list[ET.Element]:
        """Shared matching logic for ``find_entry``/``find_entry_element``."""
        target = str(Path(track_path).resolve()).replace("\\", "/").casefold()

        matches: list[ET.Element] = []
        for entry_el in self._root.iterfind("./COLLECTION/ENTRY"):
            location_el = entry_el.find("LOCATION")
            if location_el is None:
                continue
            candidate = nml_location_to_path(
                location_el.get("VOLUME", ""),
                location_el.get("DIR", ""),
                location_el.get("FILE", ""),
            )
            if candidate == target:
                matches.append(entry_el)

        if not matches:
            raise TrackNotFoundError(
                f"No ENTRY found matching path: {target!r} in {self.nml_path}"
            )

        if len(matches) > 1 and (title is not None or artist is not None):
            matches = self._filter_by_title_artist(matches, title, artist)
            if not matches:
                raise TrackNotFoundError(
                    f"No ENTRY found matching path: {target!r} in {self.nml_path} "
                    f"after applying title={title!r}/artist={artist!r} filters"
                )

        if len(matches) > 1:
            candidates = ", ".join(
                f"{e.get('ARTIST', '')!r} - {e.get('TITLE', '')!r}" for e in matches
            )
            raise AmbiguousTrackError(
                f"{len(matches)} ENTRY elements matched path: {target!r} in "
                f"{self.nml_path} ({candidates}); disambiguate with --title/--artist"
            )

        return matches

    @staticmethod
    def _filter_by_title_artist(
        matches: list[ET.Element], title: str | None, artist: str | None
    ) -> list[ET.Element]:
        """Narrow ``matches`` by case-insensitive exact TITLE/ARTIST filters.

        Implements spec section 7.3, step 6: resolving ambiguity via
        ``--title``/``--artist`` CLI filters.
        """
        filtered = matches
        if title is not None:
            filtered = [
                e for e in filtered if e.get("TITLE", "").casefold() == title.casefold()
            ]
        if artist is not None:
            filtered = [
                e
                for e in filtered
                if e.get("ARTIST", "").casefold() == artist.casefold()
            ]
        return filtered

    def find_entry_element(
        self,
        track_path: str | Path,
        title: str | None = None,
        artist: str | None = None,
    ) -> ET.Element:
        """Locate and return the raw ``<ENTRY>`` element matching ``track_path``.

        Exposed for ``nml.writer`` (spec section 2.1), which needs the live
        ``Element`` to append new ``<CUE_V2>`` children to -- it must reuse
        this exact matching logic rather than re-implementing it, per
        ``nml.writer``'s "Never re-parse or re-derive cue math" constraint.

        Args:
            track_path: See ``find_entry``.
            title: See ``find_entry``.
            artist: See ``find_entry``.

        Raises:
            TrackNotFoundError: if no ``<ENTRY>`` matches.
            AmbiguousTrackError: if more than one ``<ENTRY>`` matches.
        """
        return self._find_matching_elements(track_path, title, artist)[0]

    @property
    def tree(self) -> ET.ElementTree:
        """The parsed ``ElementTree``, for ``nml.writer`` to serialize back to disk."""
        return self._tree

    @staticmethod
    def _entry_from_element(entry_el: ET.Element) -> TrackEntry:
        """Build a ``TrackEntry`` from a matched ``<ENTRY>`` element."""
        title = entry_el.get("TITLE", "")
        artist = entry_el.get("ARTIST", "")

        location_el = entry_el.find("LOCATION")
        location_path = (
            nml_location_to_path(
                location_el.get("VOLUME", ""),
                location_el.get("DIR", ""),
                location_el.get("FILE", ""),
            )
            if location_el is not None
            else ""
        )

        tempo_el = entry_el.find("TEMPO")
        tempo = TempoInfo(
            bpm=float(tempo_el.get("BPM", "0.0")) if tempo_el is not None else 0.0,
            bpm_quality=(
                float(tempo_el.get("BPM_QUALITY", "100.0"))
                if tempo_el is not None
                else 100.0
            ),
        )

        duration_ms = NmlParser._extract_duration_ms(entry_el)

        cues: list[CuePoint] = []
        grid_anchor_ms = 0.0
        for cue_el in entry_el.findall("CUE_V2"):
            cue_type = CueType(int(cue_el.get("TYPE", "0")))
            start_ms = float(cue_el.get("START", "0.0"))
            cues.append(
                CuePoint(
                    name=cue_el.get("NAME", ""),
                    type=cue_type,
                    start_ms=start_ms,
                    len_ms=float(cue_el.get("LEN", "0.0")),
                    repeats=int(cue_el.get("REPEATS", "-1")),
                    hotcue=int(cue_el.get("HOTCUE", "-1")),
                    displ_order=int(cue_el.get("DISPL_ORDER", "0")),
                )
            )
            if cue_type == CueType.GRID:
                grid_anchor_ms = start_ms

        return TrackEntry(
            title=title,
            artist=artist,
            location_path=location_path,
            tempo=tempo,
            cues=cues,
            grid_anchor_ms=grid_anchor_ms,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _extract_duration_ms(entry_el: ET.Element) -> float:
        """Extract track duration in milliseconds from ``<INFO>``.

        Spec section 2.3 documents ``duration_ms`` as coming from
        ``PLAYTIME_FLOAT * 1000``. In practice, some Traktor versions
        (observed: Traktor Pro 4 / NML VERSION="20") omit
        ``PLAYTIME_FLOAT`` and only write the integer-seconds
        ``PLAYTIME`` attribute. This falls back to ``PLAYTIME`` (seconds)
        when ``PLAYTIME_FLOAT`` is absent, rather than failing to
        populate duration at all.
        """
        info_el = entry_el.find("INFO")
        if info_el is None:
            return 0.0

        playtime_float = info_el.get("PLAYTIME_FLOAT")
        if playtime_float is not None:
            return float(playtime_float) * 1000.0

        playtime = info_el.get("PLAYTIME")
        if playtime is not None:
            return float(playtime) * 1000.0

        return 0.0
