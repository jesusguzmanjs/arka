"""Best-effort physical audio-file metadata writing via :mod:`mutagen`."""

from __future__ import annotations

from mutagen import MutagenError
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import (
    COMM,
    IPLS,
    TIPL,
    POPM,
    TALB,
    TCON,
    TIT2,
    TPE1,
    TPE4,
    TPUB,
    TXXX,
    USLT,
    ID3,
    ID3NoHeaderError,
)
from mutagen.mp4 import AtomDataType, MP4, MP4FreeForm
from mutagen.wave import WAVE
from pathlib import Path


class MetadataWriteError(Exception):
    """A physical metadata update could not be completed."""


class UnsupportedAudioFormatError(MetadataWriteError):
    """The file extension has no CueGrid metadata writer."""


_ID3_RATING = {0: 0, 1: 1, 2: 64, 3: 128, 4: 196, 5: 255}
_ID3_TEXT_FRAMES = {
    "title": ("TIT2", TIT2),
    "release": ("TALB", TALB),
    "artist": ("TPE1", TPE1),
    "remixer": ("TPE4", TPE4),
    "genre": ("TCON", TCON),
    "label": ("TPUB", TPUB),
}
_VORBIS_KEYS = {
    "title": "TITLE",
    "release": "ALBUM",
    "artist": "ARTIST",
    "remixer": "MIXARTIST",
    "producer": "PRODUCER",
    "genre": "GENRE",
    "label": "ORGANIZATION",
    "comment": "COMMENT",
    "comment2": "COMMENT2",
    "lyrics": "LYRICS",
    "mix": "VERSION",
    "rating": "RATING",
}
_MP4_STANDARD_KEYS = {
    "title": "©nam",
    "release": "©alb",
    "artist": "©ART",
    "genre": "©gen",
    "comment": "©cmt",
    "lyrics": "©lyr",
    "label": "©pub",
}
_MP4_FREEFORM_FIELDS = {"remixer", "producer", "label", "comment2", "mix", "rating"}


def write_metadata_to_file(
    audio_path: str | Path, fields: dict[str, str | int | None]
) -> None:
    """Write one validated metadata patch to a supported physical file.

    The caller owns batch sequencing and catches :class:`MetadataWriteError`
    per track. This wrapper deliberately translates mutagen and OS exceptions
    into that single error type so file locks cannot abort an NML batch.
    """
    path = Path(audio_path)
    try:
        suffix = path.suffix.casefold()
        if suffix == ".mp3":
            _write_id3_path(path, fields)
        elif suffix == ".flac":
            _write_flac(path, fields)
        elif suffix in {".m4a", ".mp4", ".aac"}:
            _write_mp4(path, fields)
        elif suffix == ".aiff":
            _write_container_id3(AIFF(path), fields)
        elif suffix == ".wav":
            _write_container_id3(WAVE(path), fields)
        else:
            raise UnsupportedAudioFormatError(
                f"unsupported audio format: {path.suffix or '<none>'}"
            )
    except UnsupportedAudioFormatError:
        raise
    except (MutagenError, OSError, ValueError) as exc:
        raise MetadataWriteError(f"failed to write metadata to {path}: {exc}") from exc


def _write_id3_path(path: Path, fields: dict[str, str | int | None]) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    _apply_id3(tags, fields)
    # 1. EL GRAN ARREGLO DE VERSIÓN: Guardamos en v2.4 (soporta UTF-8 nativo para el GENRE)
    tags.save(path, v2_version=4)


def _write_container_id3(container: AIFF | WAVE, fields: dict[str, str | int | None]) -> None:
    if container.tags is None:
        container.add_tags()
    assert isinstance(container.tags, ID3)
    _apply_id3(container.tags, fields)
    # También v2.4 para WAV y AIFF
    container.save(v2_version=4)


def _apply_id3(tags: ID3, fields: dict[str, str | int | None]) -> None:
    for field, value in fields.items():
        if field in _ID3_TEXT_FRAMES:
            frame_id, frame_type = _ID3_TEXT_FRAMES[field]
            tags.delall(frame_id)
            if value is not None:
                tags.add(frame_type(encoding=3, text=[str(value)]))

        elif field == "producer":
            tags.delall("IPLS")
            tags.delall("TIPL") # Limpiamos ambas versiones por si acaso
            if value is not None:
                # TIPL es el estándar de ID3v2.4 para "Involved People List"
                tags.add(TIPL(encoding=3, people=[("producer", str(value))]))

        elif field == "comment":
            # 2. EL ARREGLO DEL COMENTARIO: desc="" es vital para que Traktor lo lea
            tags.delall("COMM::eng")
            tags.delall("COMM:CueGrid:eng") # Limpiamos el rastro de la prueba anterior
            if value is not None:
                tags.add(COMM(encoding=3, lang="eng", desc="", text=[str(value)]))

        elif field in {"comment2", "mix"}:
            description = field.upper()
            tags.delall(f"TXXX:{description}")
            if value is not None:
                tags.add(TXXX(encoding=3, desc=description, text=[str(value)]))

        elif field == "lyrics":
            tags.delall("USLT::eng")
            if value is not None:
                tags.add(USLT(encoding=3, lang="eng", desc="", text=str(value)))

        elif field == "rating":
            # 3. EL ARREGLO DEL RATING: Traktor solo lee POPM si viene con su email oficial
            tags.delall("POPM:traktor@native-instruments.de")
            tags.delall("POPM:cuegrid@local") # Limpiamos la prueba anterior
            if value is not None:
                tags.add(POPM(email="traktor@native-instruments.de", rating=_ID3_RATING[int(value)], count=0))

        else:
            raise ValueError(f"unsupported metadata field: {field}")

def _write_flac(path: Path, fields: dict[str, str | int | None]) -> None:
    audio = FLAC(path)
    if audio.tags is None:
        audio.add_tags()

    for field, value in fields.items():
        key = _VORBIS_KEYS.get(field)
        if key is None:
            raise ValueError(f"unsupported metadata field: {field}")

        # 1. Limpieza extrema: Borramos la llave exacta y cualquier variante
        # Usamos try/except porque Mutagen no soporta pop(key, default)
        for variant in (key, key.capitalize(), key.lower()):
            try:
                del audio.tags[variant]
            except KeyError:
                pass

        # 2. Escribimos el valor nuevo (solo si no es None)
        if value is not None:
            audio.tags[key] = [str(value)]

    audio.save()

def _write_mp4(path: Path, fields: dict[str, str | int | None]) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    for field, value in fields.items():
        if field in _MP4_STANDARD_KEYS:
            key = _MP4_STANDARD_KEYS[field]
        elif field in _MP4_FREEFORM_FIELDS:
            key = f"----:com.apple.iTunes:{field.upper()}"
        else:
            raise ValueError(f"unsupported metadata field: {field}")
        if value is None:
            audio.tags.pop(key, None)
        elif field in _MP4_STANDARD_KEYS:
            audio.tags[key] = [str(value)]
        else:
            audio.tags[key] = [
                MP4FreeForm(str(value).encode("utf-8"), dataformat=AtomDataType.UTF8)
            ]
    audio.save()
