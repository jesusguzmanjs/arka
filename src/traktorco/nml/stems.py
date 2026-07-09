"""Native stem sidecar path prediction (v2.0/v2.1 Stems Integration).

Implements deterministic prediction of Traktor Pro 4's native stem
sidecar location (``Stems/<shard>/<basename>.stem.mp4``) from a
collection ``<ENTRY>``'s ``AUDIO_ID``. See ``.openspec/2-spec.md``
section 9 for the architecture this supports (section 9.6 for the v2.1
"Smart Stems Path" root-resolution fix).

The hashing routine below (``_traktor_md5_transform_byte_array`` and
friends) is a direct port of Traktor's own non-standard
``MD5::transformByteArray`` helper, reverse-engineered by the
``zicez/traktor-stem-bridge`` project
(https://github.com/zicez/traktor-stem-bridge, MIT licensed) from the
Traktor Pro 4 macOS binary's symbol table, and reproduced here
byte-for-byte. It is *not* standard MD5 finalization -- see
``_traktor_md5_transform_byte_array``'s docstring.

This module never touches the filesystem for *predicted sidecar file*
existence -- ``resolve_stem_path`` only joins path components for that
part; that final existence check is left to callers (``core.pipeline``),
consistent with every other read-only module in this project (spec
section 2.1). The one exception is ``Stems/`` root resolution (section
9.6 below), which reads ``Traktor Settings.tsi`` (the definitive source
of truth for where Traktor itself stores stems) and/or checks directory
existence to pick among the auto-discovery candidates.
"""

from __future__ import annotations

import base64
import logging
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from traktorco.nml.models import TrackEntry

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# FLAGS bitmask (spec section 9.1)
#
# Traktor's <INFO FLAGS="..."> is an undocumented bitfield. Comparing a
# track known to have a native stem (FLAGS="76") against otherwise
# similar tracks without one (FLAGS="12", see
# tests/fixtures/sample_collection.nml) shows the difference is exactly
# bit 0x40 (64): 76 - 12 == 64. Testing the bit -- rather than the exact
# literal value 76 -- means this still works alongside whatever other
# independent flag bits Traktor sets for a given track.
# --------------------------------------------------------------------------
STEM_FLAG_BIT = 0x40


def has_stem_flag(flags: int | None) -> bool:
    """Return ``True`` if ``<INFO FLAGS="...">`` indicates a native stem.

    ``flags`` is ``None`` when the ``<ENTRY>`` had no ``<INFO>`` element
    or no ``FLAGS`` attribute -- always ``False`` in that case.
    """
    return flags is not None and bool(flags & STEM_FLAG_BIT)


# --------------------------------------------------------------------------
# Traktor's non-standard MD5-based hash routine, ported from
# traktor-stem-bridge's ``sidecar.py``.
# --------------------------------------------------------------------------
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
TRACK_ID_SIZE = 0x100
_INITIAL_STATE = (0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476)
_SHIFTS = (
    [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
)
_TABLE = (
    0xD76AA478,
    0xE8C7B756,
    0x242070DB,
    0xC1BDCEEE,
    0xF57C0FAF,
    0x4787C62A,
    0xA8304613,
    0xFD469501,
    0x698098D8,
    0x8B44F7AF,
    0xFFFF5BB1,
    0x895CD7BE,
    0x6B901122,
    0xFD987193,
    0xA679438E,
    0x49B40821,
    0xF61E2562,
    0xC040B340,
    0x265E5A51,
    0xE9B6C7AA,
    0xD62F105D,
    0x02441453,
    0xD8A1E681,
    0xE7D3FBC8,
    0x21E1CDE6,
    0xC33707D6,
    0xF4D50D87,
    0x455A14ED,
    0xA9E3E905,
    0xFCEFA3F8,
    0x676F02D9,
    0x8D2A4C8A,
    0xFFFA3942,
    0x8771F681,
    0x6D9D6122,
    0xFDE5380C,
    0xA4BEEA44,
    0x4BDECFA9,
    0xF6BB4B60,
    0xBEBFBC70,
    0x289B7EC6,
    0xEAA127FA,
    0xD4EF3085,
    0x04881D05,
    0xD9D4D039,
    0xE6DB99E5,
    0x1FA27CF8,
    0xC4AC5665,
    0xF4292244,
    0x432AFF97,
    0xAB9423A7,
    0xFC93A039,
    0x655B59C3,
    0x8F0CCC92,
    0xFFEFF47D,
    0x85845DD1,
    0x6FA87E4F,
    0xFE2CE6E0,
    0xA3014314,
    0x4E0811A1,
    0xF7537E82,
    0xBD3AF235,
    0x2AD7D2BB,
    0xEB86D391,
)  # noqa: E501 -- straight port of Traktor's MD5-like transform table


@dataclass(frozen=True)
class SidecarPrediction:
    """Predicted native stem sidecar location (shard folder + basename)."""

    shard: int
    basename: str

    @property
    def filename(self) -> str:
        """The sidecar's bare filename, e.g. ``"XXXX...XXXX.stem.mp4"``."""
        return f"{self.basename}.stem.mp4"

    @property
    def shard_dir(self) -> str:
        """Zero-padded, 3-digit shard folder name, e.g. ``"097"``."""
        return f"{self.shard:03d}"


def decode_audio_id(audio_id: str) -> bytes:
    """Decode Traktor's base64 ``AUDIO_ID``/``TrackID``, tolerating missing padding."""
    padding = "=" * ((4 - len(audio_id) % 4) % 4)
    return base64.b64decode(audio_id + padding)


def predict_sidecar_from_track_id(track_id: bytes) -> SidecarPrediction:
    """Predict the native sidecar path components for a raw 256-byte TrackID."""
    if len(track_id) != TRACK_ID_SIZE:
        raise ValueError(f"TrackID must be {TRACK_ID_SIZE} bytes, got {len(track_id)}")

    words = _traktor_md5_transform_byte_array(track_id)
    shard = words[0] & 0x7F
    basename = _md5_words_to_string(words)
    return SidecarPrediction(shard=shard, basename=basename)


def predict_sidecar(audio_id: str) -> SidecarPrediction:
    """Predict the native sidecar path components for a collection ``AUDIO_ID``."""
    return predict_sidecar_from_track_id(decode_audio_id(audio_id))


def _rotate_left_32(value: int, count: int) -> int:
    return ((value << count) | (value >> (32 - count))) & 0xFFFFFFFF


def _traktor_md5_transform_byte_array(data: bytes) -> tuple[int, int, int, int]:
    """Reproduce Traktor's ``MD5::transformByteArray`` helper.

    This is *not* standard MD5 finalization. It uses standard MD5
    compression rounds, then processes one final zero-padded 64-byte
    block without appending MD5's normal ``0x80`` byte or bit-length
    footer.
    """
    state = list(_INITIAL_STATE)
    full_length = len(data) // 64 * 64
    blocks = [data[offset : offset + 64] for offset in range(0, full_length, 64)]
    remainder = data[full_length:]
    blocks.append(remainder + b"\0" * (64 - len(remainder)))

    for block in blocks:
        words = list(struct.unpack("<16I", block))
        a, b, c, d = state
        original = (a, b, c, d)

        for index in range(64):
            if index < 16:
                func = (b & c) | (~b & d)
                word_index = index
            elif index < 32:
                func = (d & b) | (~d & c)
                word_index = (5 * index + 1) % 16
            elif index < 48:
                func = b ^ c ^ d
                word_index = (3 * index + 5) % 16
            else:
                func = c ^ (b | ~d)
                word_index = (7 * index) % 16

            value = (a + func + _TABLE[index] + words[word_index]) & 0xFFFFFFFF
            a, d, c, b = (
                d,
                c,
                b,
                (b + _rotate_left_32(value, _SHIFTS[index])) & 0xFFFFFFFF,
            )

        state = [
            (current + delta) & 0xFFFFFFFF
            for current, delta in zip(original, (a, b, c, d))
        ]

    return (state[0], state[1], state[2], state[3])


def _md5_words_to_string(words: tuple[int, int, int, int]) -> str:
    chars = []
    for word in words:
        for shift in (0, 5, 10, 15, 20, 25, 30):
            chars.append(ALPHABET[(word >> shift) & 0x1F])
    return "".join(chars)


# --------------------------------------------------------------------------
# Project-specific integration: turn a prediction into an absolute path
# next to a given collection.nml.
# --------------------------------------------------------------------------
TSI_SETTINGS_FILENAME = "Traktor Settings.tsi"
TSI_STEMS_DIR_ENTRY_NAME = "Browser.Dir.GeneratedStems"


def read_stems_dir_from_settings(nml_path: str | Path) -> Path | None:
    """Read the user's actual configured Stems directory from ``Traktor
    Settings.tsi``, Traktor's own preferences file (spec section 9.6).

    ``Traktor Settings.tsi`` is valid XML and lives in the same directory
    as ``collection.nml``. It contains an
    ``<Entry Name="Browser.Dir.GeneratedStems" ... Value="...">`` node
    holding the absolute path Traktor itself uses -- this is the
    definitive source of truth, ahead of any guessed default.

    Returns ``None`` (never raises) if the ``.tsi`` file does not exist,
    is not parseable XML, or has no matching ``<Entry>`` -- callers must
    treat that as "unavailable" and fall through to the next candidate
    in the resolution chain, consistent with this module's read-only,
    graceful-degradation design (spec section 2.1).
    """
    tsi_path = Path(nml_path).parent / TSI_SETTINGS_FILENAME
    if not tsi_path.is_file():
        return None

    try:
        root = ET.parse(tsi_path).getroot()
    except ET.ParseError as exc:
        logger.warning("Failed to parse %s: %s", tsi_path, exc)
        return None

    for entry in root.iter("Entry"):
        if entry.get("Name") == TSI_STEMS_DIR_ENTRY_NAME:
            value = entry.get("Value")
            return Path(value) if value else None

    return None


def _default_stems_root(nml_path: str | Path) -> Path:
    """Auto-discover the ``Stems/`` root directory (spec section 9.6).

    Resolution order, once an explicit ``stems_dir`` override has already
    been ruled out by the caller:

    1. The path recorded in ``Traktor Settings.tsi``'s own
       ``Browser.Dir.GeneratedStems`` entry -- the definitive source of
       truth, since it is exactly what Traktor itself uses.
    2. If the ``.tsi`` is missing/unreadable/has no such entry, the OS's
       native Music folder, ``~/Music/Traktor/Stems/`` (resolved safely
       and cross-platform via ``pathlib.Path.home()``) -- Traktor's own
       documented default install location.
    3. If that directory does not exist on disk either, fall back to the
       v2.0 assumption of a ``Stems/`` folder next to the NML, which
       remains correct for some custom Traktor installs/versions.
    """
    from_settings = read_stems_dir_from_settings(nml_path)
    if from_settings is not None:
        return from_settings

    native_music_stems = Path.home() / "Music" / "Traktor" / "Stems"
    if native_music_stems.is_dir():
        return native_music_stems
    return Path(nml_path).parent / "Stems"


def resolve_stem_path(
    entry: TrackEntry,
    nml_path: str | Path,
    stems_dir: str | Path | None = None,
) -> Path | None:
    """Resolve the absolute, predicted native stem sidecar path for ``entry``.

    Implements the "Path Prediction" step of the v2.0 Stems Integration
    architecture (spec section 9.2), plus the v2.1 "Smart Stems Path"
    root-resolution fix (spec section 9.6): deterministically compute
    ``<Stems root>/<shard>/<basename>.stem.mp4`` from ``entry.audio_id``.

    ``<Stems root>`` is resolved as follows:

    1. If ``stems_dir`` is given explicitly, it is used verbatim -- no
       auto-discovery, no existence checks against alternatives.
    2. Otherwise, read ``Browser.Dir.GeneratedStems`` from ``Traktor
       Settings.tsi`` (a sibling of ``collection.nml``) -- the definitive
       source of truth, since it is the exact path Traktor itself uses
       (see ``read_stems_dir_from_settings``).
    3. If the ``.tsi`` is missing, unparseable, or has no matching entry,
       try Traktor's documented default install location: the OS's
       native Music folder, ``~/Music/Traktor/Stems/`` (via
       ``pathlib.Path.home()``, safe across Windows/macOS/Linux).
    4. If that directory does not exist on disk either, fall back to the
       v2.0 assumption: ``Stems/`` as a sibling of ``collection.nml``
       inside a given Traktor version directory (e.g. ``~/Documents/
       Native Instruments/Traktor 4.5.0/Stems/``).

    This function performs no filesystem I/O for the *predicted sidecar
    file* itself -- only the ``<Stems root>``-selection step above
    touches disk, to read the ``.tsi`` settings file and/or check
    directory existence between auto-discovery candidates. Callers
    (``core.pipeline``) remain responsible for checking the final
    ``Path.is_file()`` before using the result, consistent with every
    other read-only module in this project (spec section 2.1).

    Args:
        entry: The parsed ``TrackEntry``; requires a non-empty ``audio_id``.
        nml_path: Path to the ``collection.nml`` this entry came from --
            used to locate the NML-sibling ``Stems/`` fallback root.
        stems_dir: Optional explicit override for the ``Stems/`` root
            directory (e.g. from ``--stems-dir``/``AppConfig.stems_dir``
            when the user has repointed Traktor's stem storage location).
            When given, auto-discovery is skipped entirely.

    Returns:
        The predicted absolute ``Path``, or ``None`` if ``entry.audio_id``
        is missing/empty (e.g. an older collection entry Traktor has not
        assigned a TrackID to).
    """
    if not entry.audio_id:
        return None

    prediction = predict_sidecar(entry.audio_id)
    stems_root = (
        Path(stems_dir) if stems_dir is not None else _default_stems_root(nml_path)
    )
    return stems_root / prediction.shard_dir / prediction.filename
