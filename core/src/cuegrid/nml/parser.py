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

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from cuegrid.nml.constants import CueType
from cuegrid.nml.models import CuePoint, TempoInfo, TrackEntry

_WINDOWS_VOLUME_RE = re.compile(r"^[A-Za-z]:$")

# Traktor stores its native key value as an integer in ``<MUSICAL_KEY>``.
# The order is not chromatic-circle order, so retain the explicit mapping.
TRAKTOR_MUSICAL_KEY_TO_OPEN_KEY = {
    0: "1d", 1: "8d", 2: "3d", 3: "10d", 4: "5d", 5: "12d",
    6: "7d", 7: "2d", 8: "9d", 9: "4d", 10: "11d", 11: "6d",
    12: "1m", 13: "8m", 14: "3m", 15: "10m", 16: "5m", 17: "12m",
    18: "7m", 19: "2m", 20: "9m", 21: "4m", 22: "11m", 23: "6m",
}

_CAMELOT_TO_OPEN_KEY_NUMBER = {
    1: 6, 2: 11, 3: 8, 4: 9, 5: 10, 6: 7,
    7: 12, 8: 1, 9: 2, 10: 3, 11: 4, 12: 5,
}
_MAJOR_NOTE_TO_OPEN_KEY = {
    "c": "1d", "c#": "8d", "db": "8d", "d": "3d", "d#": "10d",
    "eb": "10d", "e": "5d", "f": "12d", "f#": "7d", "gb": "7d",
    "g": "2d", "g#": "9d", "ab": "9d", "a": "4d", "a#": "11d",
    "bb": "11d", "b": "6d",
}
_MINOR_NOTE_TO_OPEN_KEY = {
    "g#": "6m", "ab": "6m", "d#": "11m", "eb": "11m", "a#": "8m",
    "bb": "8m", "f": "9m", "c": "10m", "g": "7m", "d": "12m",
    "a": "1m", "e": "2m", "b": "3m", "f#": "4m", "gb": "4m",
    "c#": "5m", "db": "5m",
}
_OPEN_KEY_RE = re.compile(r"^(1[0-2]|[1-9])([dm])$", re.IGNORECASE)
_CAMELOT_KEY_RE = re.compile(r"^(1[0-2]|[1-9])([ab])$", re.IGNORECASE)
_LEGACY_KEY_RE = re.compile(
    r"^([a-g])([#b♯♭]?)(?:\s*(major|maj|minor|min|m))?$", re.IGNORECASE
)

logger = logging.getLogger(__name__)

TRAKTOR_SYSTEM_PLAYLISTS = ["_LOOPS", "_RECORDINGS"]


def normalize_to_open_key(key_str: str) -> str:
    """Normalize a Traktor, Camelot, or conventional key label to Open Key.

    Valid results are always Traktor's native ``1d``--``12d`` (major/Dur) or
    ``1m``--``12m`` (minor/Moll) labels. Empty or unrecognized input returns
    an empty string so callers can represent an absent key without leaking a
    non-Open-Key value into CueGrid.
    """
    if not isinstance(key_str, str):
        return ""

    value = key_str.strip()
    if not value:
        return ""

    if open_match := _OPEN_KEY_RE.fullmatch(value):
        return f"{open_match.group(1)}{open_match.group(2).lower()}"
    if camelot_match := _CAMELOT_KEY_RE.fullmatch(value):
        number = _CAMELOT_TO_OPEN_KEY_NUMBER[int(camelot_match.group(1))]
        suffix = "m" if camelot_match.group(2).casefold() == "a" else "d"
        return f"{number}{suffix}"

    legacy_match = _LEGACY_KEY_RE.fullmatch(value)
    if legacy_match is None:
        return ""

    note = f"{legacy_match.group(1).casefold()}{legacy_match.group(2)}"
    note = note.replace("♯", "#").replace("♭", "b")
    mode = legacy_match.group(3)
    # A bare conventional note (for example, "C") conventionally means major.
    return (_MINOR_NOTE_TO_OPEN_KEY if mode and mode.casefold() in {"minor", "min", "m"} else _MAJOR_NOTE_TO_OPEN_KEY).get(note, "")


class TrackNotFoundError(Exception):
    """Raised when no ``<ENTRY>`` in the collection matches the requested path."""


class AmbiguousTrackError(Exception):
    """Raised when more than one ``<ENTRY>`` matches the requested path."""


class PlaylistNotFoundError(Exception):
    """Raised when no ``<NODE TYPE="PLAYLIST">`` matches the requested name."""


class AmbiguousPlaylistError(Exception):
    """Raised when more than one ``<NODE TYPE="PLAYLIST">`` matches the requested name."""


class DuplicateLocationError(ValueError):
    """Raised when two collection entries normalize to the same location."""

    def __init__(self, location_path: str) -> None:
        self.location_path = location_path
        super().__init__(f"Duplicate collection LOCATION: {location_path!r}")


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
        # macOS mounts its boot volume at /, rather than under /Volumes.
        if volume == "Macintosh HD":
            raw = str(PurePosixPath("/", *segments, file_))
        else:
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

    def get_track_metadata(
        self,
        track_path: str | Path,
        title: str | None = None,
        artist: str | None = None,
    ) -> dict[str, Any]:
        """Locate the matching ``<ENTRY>`` and shape it into the
        ``--get-track-metadata`` success schema (``.openspec/3-player-spec.md``
        section 1.3).

        Args:
            track_path: See ``find_entry``.
            title: See ``find_entry``.
            artist: See ``find_entry``.

        Returns:
            A JSON-serializable dict with ``artist``, ``title``, ``bpm``,
            ``grid_anchor_ms``, and ``existing_cues`` (section 1.3).

        Raises:
            TrackNotFoundError: see ``find_entry``.
            AmbiguousTrackError: see ``find_entry``.
        """
        entry = self.find_entry(track_path, title=title, artist=artist)
        return self._track_entry_to_metadata_dict(entry)

    def get_library(self) -> dict[str, Any]:
        """Build the relational Global Collection export from the live tree.

        The collection is indexed once by normalized ``LOCATION``. Playlist
        entries are then reduced to normalized ``PRIMARYKEY`` references, so
        track metadata is never copied into playlist nodes. Iteration stays on
        direct children of the relevant XML nodes to avoid broad descendant
        searches and temporary lists for large libraries.

        Raises:
            DuplicateLocationError: if two collection entries have the same
                normalized location and the export would lose one of them.
        """
        collection: dict[str, dict[str, Any]] = {}
        collection_el = self._root.find("COLLECTION")

        if collection_el is not None:
            collection_index = 0
            for entry_el in collection_el:
                if entry_el.tag != "ENTRY":
                    continue

                track_entry = self._entry_from_element(entry_el)
                location_path = track_entry.location_path
                if location_path in collection:
                    raise DuplicateLocationError(location_path)

                metadata = self._track_entry_to_metadata_dict(track_entry)
                metadata.update(
                    {
                        **self._editable_metadata_dict(track_entry),
                        "location_path": location_path,
                        "key": track_entry.key,
                        "duration_ms": track_entry.duration_ms,
                        "collection_index": collection_index,
                    }
                )
                collection[location_path] = metadata
                collection_index += 1

        playlists_el = self._root.find("PLAYLISTS")
        playlists = (
            self._playlist_nodes_to_payload(playlists_el, collection)
            if playlists_el is not None
            else []
        )
        return {"collection": collection, "playlists": playlists}

    @staticmethod
    def _editable_metadata_dict(entry: TrackEntry) -> dict[str, Any]:
        """Return the complete editable metadata portion of a library row.

        Library rows deliberately materialize absent NML fields as empty
        strings (and an absent/invalid ranking as zero) so the GUI never has
        to make a second query before rendering an editable column.
        """
        return {
            "title": entry.title,
            "artist": entry.artist,
            "album": entry.album,
            "remixer": entry.remixer,
            "producer": entry.producer,
            "genre": entry.genre,
            "label": entry.label,
            "comment": entry.comment,
            "comment2": entry.comment2,
            "lyrics": entry.lyrics,
            "mix": entry.mix,
            "rating": entry.rating,
        }

    def _playlist_nodes_to_payload(
        self,
        playlists_el: ET.Element,
        collection: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert direct ``PLAYLISTS`` children without embedding tracks."""
        nodes: list[dict[str, Any]] = []
        for child in playlists_el:
            if child.tag != "NODE":
                continue
            node = self._playlist_node_to_payload(child, collection)
            if node is not None:
                nodes.append(node)
        return nodes

    def _playlist_node_to_payload(
        self,
        node_el: ET.Element,
        collection: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Convert one playlist/folder node and recursively retain its shape."""
        node_type = node_el.get("TYPE")
        name = node_el.get("NAME", "")

        if node_type == "FOLDER":
            children: list[dict[str, Any]] = []
            for child_container in node_el:
                if child_container.tag != "SUBNODES":
                    continue
                for child_node in child_container:
                    if child_node.tag != "NODE":
                        continue
                    child_payload = self._playlist_node_to_payload(
                        child_node, collection
                    )
                    if child_payload is not None:
                        children.append(child_payload)
            return {"kind": "folder", "name": name, "children": children}

        if node_type == "PLAYLIST":
            if name in TRAKTOR_SYSTEM_PLAYLISTS:
                return None

            playlist_el = node_el.find("PLAYLIST")
            track_paths: list[str] = []
            if playlist_el is not None:
                for playlist_entry_el in playlist_el:
                    if playlist_entry_el.tag != "ENTRY":
                        continue
                    primarykey_el = playlist_entry_el.find("PRIMARYKEY")
                    if primarykey_el is None or primarykey_el.get("TYPE") != "TRACK":
                        continue

                    key = primarykey_el.get("KEY", "")
                    if not key:
                        continue
                    try:
                        normalized_path = primary_key_to_normalized_path(key)
                    except (ValueError, IndexError):
                        logger.warning(
                            "Skipping stale/malformed PRIMARYKEY in playlist %r: KEY=%r",
                            name,
                            key,
                        )
                        continue

                    if normalized_path not in collection:
                        logger.warning(
                            "Skipping unresolved PRIMARYKEY in playlist %r: KEY=%r",
                            name,
                            key,
                        )
                        continue
                    track_paths.append(normalized_path)

            return {
                "kind": "playlist",
                "uuid": playlist_el.get("UUID", ""),
                "name": name,
                "track_paths": track_paths,
            }

        return None

    @staticmethod
    def _track_entry_to_metadata_dict(entry: TrackEntry) -> dict[str, Any]:
        """Shape a ``TrackEntry`` into the section 1.3 success schema dict.

        Implements the ``existing_cues`` shaping rules from section 1.3:
        excludes ``CueType.GRID`` (already surfaced via ``grid_anchor_ms``),
        sorts ascending by ``start_ms``, and serializes ``type`` as the
        ``CueType`` member's string name rather than its integer value.
        """
        existing_cues = sorted(
            (cue for cue in entry.cues if cue.type != CueType.GRID),
            key=lambda cue: cue.start_ms,
        )
        return {
            "artist": entry.artist,
            "title": entry.title,
            "bpm": entry.tempo.bpm,
            "grid_anchor_ms": entry.grid_anchor_ms,
            "is_flex_grid": entry.is_flex_grid,
            "existing_cues": [
                {
                    "hotcue": cue.hotcue,
                    "name": cue.name,
                    "start_ms": cue.start_ms,
                    "type": cue.type.name,
                }
                for cue in existing_cues
            ],
        }

    @property
    def tree(self) -> ET.ElementTree:
        """The parsed ``ElementTree``, for ``nml.writer`` to serialize back to disk."""
        return self._tree

    def restore_tree(self, tree: ET.ElementTree) -> None:
        """Restore a previously retained tree after a failed mutation write."""
        self._tree = tree
        self._root = tree.getroot()

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
        musical_key_el = entry_el.find("MUSICAL_KEY")
        key = ""
        if musical_key_el is not None:
            try:
                key = TRAKTOR_MUSICAL_KEY_TO_OPEN_KEY.get(
                    int(musical_key_el.get("VALUE", "")), ""
                )
            except ValueError:
                pass
        if not key and info_el is not None:
            key = normalize_to_open_key(info_el.get("KEY", ""))
        flags: int | None = None
        album_el = entry_el.find("ALBUM")
        album = album_el.get("TITLE", "") if album_el is not None else ""
        remixer = producer = genre = label = comment = comment2 = lyrics = mix = ""
        rating = 0
        if info_el is not None:
            remixer = info_el.get("REMIXER", "")
            producer = info_el.get("PRODUCER", "")
            genre = info_el.get("GENRE", "")
            label = info_el.get("LABEL", "")
            comment = info_el.get("COMMENT", "")
            comment2 = info_el.get("RATING", "")
            lyrics = info_el.get("KEY_LYRICS", "")
            mix = info_el.get("MIX", "")
            rating = NmlParser._extract_rating(info_el.get("RANKING"))
            flags_str = info_el.get("FLAGS")
            if flags_str is not None:
                flags = int(flags_str)

        cues: list[CuePoint] = []
        grid_anchor_ms = 0.0
        grid_marker_count = 0
        for cue_el in entry_el:
            if cue_el.tag != "CUE_V2":
                continue
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
                grid_marker_count += 1
                grid_anchor_ms = start_ms

        return TrackEntry(
            title=title,
            artist=artist,
            location_path=location_path,
            tempo=tempo,
            cues=cues,
            grid_anchor_ms=grid_anchor_ms,
            is_flex_grid=grid_marker_count > 1,
            duration_ms=duration_ms,
            key=key or None,
            album=album,
            remixer=remixer,
            producer=producer,
            genre=genre,
            label=label,
            comment=comment,
            comment2=comment2,
            lyrics=lyrics,
            mix=mix,
            rating=rating,
            peak_db=peak_db,
            perceived_db=perceived_db,
            audio_id=audio_id,
            flags=flags,
        )

    @staticmethod
    def _extract_rating(ranking: str | None) -> int:
        """Convert Traktor's 0-255 ``RANKING`` attribute to a 0-5 rating."""
        if ranking is None:
            return 0
        try:
            return max(0, min(5, round(float(ranking) / 51.0)))
        except (TypeError, ValueError):
            return 0

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
                logger.warning(
                    "Skipping unresolved PRIMARYKEY in playlist %r: KEY=%r",
                    playlist_name,
                    key,
                )
                continue
            except AmbiguousTrackError:
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

    def list_playlist_names(self) -> list[str]:
        """Return every playlist NAME in the collection, in document order.

        Recurses the whole <PLAYLISTS> subtree (arbitrary FOLDER nesting,
        same traversal as find_entries_by_playlist, spec section 8.1.2)
        and collects the NAME attribute of every <NODE TYPE="PLAYLIST">.
        Unlike find_entries_by_playlist, duplicate names are not an error
        here -- this is a pure listing, not a lookup-by-name, so both are
        returned as-is, in the order they appear in the file.

        Implements spec section 12.2. Never raises
        PlaylistNotFoundError/AmbiguousPlaylistError: an empty
        <PLAYLISTS> tree (or a collection with zero playlists) simply
        yields an empty list.

        Returns:
            A list of playlist names, possibly containing duplicates,
            possibly empty.
        """
        return [
            node.get("NAME", "")
            for node in self._root.iter("NODE")
            if node.get("TYPE") == "PLAYLIST"
            and node.get("NAME", "") not in TRAKTOR_SYSTEM_PLAYLISTS
        ]



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
