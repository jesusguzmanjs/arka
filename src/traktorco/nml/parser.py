"""Read-only NML parsing.

Implements the responsibilities described in ``.openspec/2-spec.md``
section 2.1 (``nml.parser``) and section 7 (``LOCATION`` matching and path
normalization): locate an ``<ENTRY>`` in a Traktor ``collection.nml`` by
matching its ``<LOCATION>`` against a user-supplied audio file path, then
extract ``TempoInfo``, the grid anchor, track duration, and any existing
``CuePoint``s into a ``TrackEntry``.

Also implements section 8 (batch processing) functions to resolve tracks
by playlist name or title.

This module never mutates the parsed tree -- see ``nml/writer.py`` for
writing cues back to the file.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from traktorco.nml.constants import CueType
from traktorco.nml.models import CuePoint, TempoInfo, TrackEntry

_WINDOWS_VOLUME_RE = re.compile(r"^[A-Za-z]:$")


class TrackNotFoundError(Exception):
    """Raised when no ``<ENTRY>`` in the collection matches the requested path."""


class AmbiguousTrackError(Exception):
    """Raised when more than one ``<ENTRY>`` matches the requested path."""


class PlaylistNotFoundError(Exception):
    """Raised when no ``<NODE TYPE="PLAYLIST">`` matches the requested name."""


class AmbiguousPlaylistError(Exception):
    """Raised when more than one ``<NODE TYPE="PLAYLIST">`` matches the requested name."""


@dataclass
class BatchTrackRef:
    """One track resolved for batch processing: its parsed data, plus
    the live <ENTRY> Element it came from, so core.pipeline/nml.writer
    never need to re-match it by path (spec section 8.3).
    """

    entry: TrackEntry
    element: ET.Element


def primary_key_to_normalized_path(key: str) -> str:
    """Convert a <PRIMARYKEY> KEY into the same normalized path string
    that nml_location_to_path() produces for a <LOCATION>, by splitting
    on the shared "/:" separator and reusing that function directly.

    Implements ``.openspec/2-spec.md`` section 8.1.1.

    Args:
        key: The PRIMARYKEY element's KEY attribute (e.g.
            "C:/:Users/:dj/:Music/:Track.flac").

    Returns:
        A normalized, comparable path string identical to what
        nml_location_to_path would produce.
    """
    segments = key.split("/:")
    volume, *dir_segments, file_ = segments
    dir_ = "/:" + "/:".join(dir_segments) + "/:"
    return nml_location_to_path(volume, dir_, file_)


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

        # v1.9: extract Traktor's auto-gain loudness metadata
        loudness_el = entry_el.find("LOUDNESS")
        peak_db: float | None = None
        perceived_db: float | None = None
        if loudness_el is not None:
            peak_str = loudness_el.get("PEAK_DB")
            perceived_str = loudness_el.get("PERCEIVED_DB")
            if peak_str is not None:
                peak_db = float(peak_str)
            if perceived_str is not None:
                perceived_db = float(perceived_str)

        # v2.0 stems: AUDIO_ID lives on <ENTRY> itself; FLAGS lives on <INFO>.
        audio_id = entry_el.get("AUDIO_ID") or None
        info_el = entry_el.find("INFO")
        flags: int | None = None
        if info_el is not None:
            flags_str = info_el.get("FLAGS")
            if flags_str is not None:
                flags = int(flags_str)

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
            peak_db=peak_db,
            perceived_db=perceived_db,
            audio_id=audio_id,
            flags=flags,
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

    def find_entries_by_playlist(self, playlist_name: str) -> list[BatchTrackRef]:
        """Resolve every track in the named playlist to a BatchTrackRef,
        in playlist order, per section 8.1 of the spec.

        Raises PlaylistNotFoundError / AmbiguousPlaylistError for the
        playlist lookup itself; per-track resolution failures (missing
        track, ambiguous match) are skipped with a warning, never raised.

        Args:
            playlist_name: The exact case-sensitive name of the playlist
                ("NAME" attribute of the ``<NODE TYPE="PLAYLIST">`` element).

        Returns:
            A list of ``BatchTrackRef`` objects in playlist order, possibly
            empty if all tracks in the playlist failed resolution.

        Raises:
            PlaylistNotFoundError: if no ``<NODE TYPE="PLAYLIST">`` with
                the given ``NAME`` exists.
            AmbiguousPlaylistError: if more than one playlist with the same
                ``NAME`` exists.
        """
        # Find the playlist node
        playlist_nodes = []
        for node in self._root.iter("NODE"):
            if node.get("TYPE") == "PLAYLIST" and node.get("NAME") == playlist_name:
                playlist_nodes.append(node)

        if not playlist_nodes:
            raise PlaylistNotFoundError(
                f"No playlist found with NAME={playlist_name!r}"
            )
        if len(playlist_nodes) > 1:
            raise AmbiguousPlaylistError(
                f"{len(playlist_nodes)} playlists found with NAME={playlist_name!r}"
            )

        playlist_node = playlist_nodes[0]
        playlist_el = playlist_node.find("PLAYLIST")
        if playlist_el is None:
            # Shouldn't happen, but be defensive
            return []

        results: list[BatchTrackRef] = []
        for entry_el in playlist_el.findall("ENTRY"):
            primarykey_el = entry_el.find("PRIMARYKEY")
            if primarykey_el is None or primarykey_el.get("TYPE") != "TRACK":
                continue

            key = primarykey_el.get("KEY", "")
            if not key:
                continue

            # Convert playlist key to normalized path
            try:
                normalized_key = primary_key_to_normalized_path(key)
            except (ValueError, IndexError):
                # Malformed key; skip and continue
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(
                    "Skipping stale/malformed PRIMARYKEY in playlist %r: KEY=%r",
                    playlist_name,
                    key,
                )
                continue

            # Find matching entry in collection
            try:
                matching_els = self._find_matching_elements(
                    normalized_key, title=None, artist=None
                )
            except TrackNotFoundError:
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(
                    "Skipping unresolved PRIMARYKEY in playlist %r: KEY=%r",
                    playlist_name,
                    key,
                )
                continue
            except AmbiguousTrackError:
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(
                    "Skipping ambiguous PRIMARYKEY in playlist %r: KEY=%r",
                    playlist_name,
                    key,
                )
                continue

            # Successful match
            matched_entry_el = matching_els[0]
            track_entry = self._entry_from_element(matched_entry_el)
            results.append(BatchTrackRef(entry=track_entry, element=matched_entry_el))

        return results

    def find_entries_by_title(
        self, title: str, artist: str | None = None
    ) -> list[BatchTrackRef]:
        """Resolve every <ENTRY> whose TITLE matches (case-insensitive exact
        match), optionally further narrowed by artist (same semantics).

        Implements section 8.2 of the spec.

        Args:
            title: The track title to match (case-insensitive).
            artist: Optional artist to further narrow the search
                (case-insensitive).

        Returns:
            A list of ``BatchTrackRef`` objects matching the criteria,
            in collection order.

        Raises:
            TrackNotFoundError: if zero ENTRYs match.
        """
        # Collect all entries from collection
        all_entries = list(self._root.iterfind("./COLLECTION/ENTRY"))
        if not all_entries:
            raise TrackNotFoundError("No entries found in collection")

        # Filter by title and artist using the existing method
        matches = self._filter_by_title_artist(all_entries, title, artist)

        if not matches:
            raise TrackNotFoundError(
                f"No ENTRY found with TITLE={title!r} and ARTIST={artist!r}"
            )

        # Convert matching elements to BatchTrackRef
        results = []
        for entry_el in matches:
            track_entry = self._entry_from_element(entry_el)
            results.append(BatchTrackRef(entry=track_entry, element=entry_el))

        return results
