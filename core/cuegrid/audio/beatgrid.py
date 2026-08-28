"""Grid-guided phrase candidate math.

Implements the pure math described in ``.openspec/2-spec.md`` section 4:
computing every plausible phrase-boundary timestamp (every 16 and 32 beats)
directly from Traktor's own BPM and grid anchor, *before* any audio is
decoded or analyzed.

Per the module boundaries in section 2.1 of the spec, this module has no
knowledge of files, XML, or ``librosa`` itself -- it is pure arithmetic on
floats/ints so it can be tested and reasoned about in isolation. Because
every candidate produced here is already an exact grid multiple
(``G + k*L`` for an integer ``k``), no downstream quantization/snapping
step is needed (spec section 4.4, item 4).
"""

from __future__ import annotations

from dataclasses import dataclass

MS_PER_MINUTE = 60_000.0


def beat_length_ms(bpm: float) -> float:
    """Return the duration of one beat, in milliseconds, for ``bpm``.

    See ``.openspec/2-spec.md`` section 4.2::

        L = 60000 / BPM

    Args:
        bpm: Track tempo, from ``<TEMPO BPM="...">``.

    Returns:
        Beat length in milliseconds.

    Raises:
        ValueError: if ``bpm`` is not strictly positive (spec section 4.4,
            item 2: zero/undefined BPM must never divide-by-zero).
    """
    if bpm <= 0:
        raise ValueError(f"BPM must be positive, got {bpm!r}")
    return MS_PER_MINUTE / bpm


@dataclass
class PhraseCandidate:
    """A single grid-locked phrase-boundary candidate (spec section 4.5).

    Attributes:
        beat_index: Offset in beats from the grid anchor. Always a multiple
            of ``phrase_beats``.
        time_ms: ``G + beat_index * L``. Already an exact grid multiple --
            never needs snapping.
        is_major_phrase: ``True`` for every ``major_phrase_multiple``-th
            candidate (an 8-bar/32-beat boundary, with the defaults).
    """

    beat_index: int
    time_ms: float
    is_major_phrase: bool


def generate_phrase_candidates(
    bpm: float,
    grid_anchor_ms: float,
    duration_ms: float,
    phrase_beats: int = 16,
    major_phrase_multiple: int = 2,
) -> list[PhraseCandidate]:
    """Enumerate every phrase-boundary candidate across a track's duration.

    Implements the generative formula from ``.openspec/2-spec.md``
    section 4.3::

        t_ms(n) = G + n * P * L,   for n = 0, 1, 2, ... while t_ms(n) <= D

    where ``L = beat_length_ms(bpm)``, ``G = grid_anchor_ms``,
    ``P = phrase_beats``, and ``D = duration_ms``. Every ``n`` for which
    ``n % major_phrase_multiple == 0`` is additionally tagged as a "major"
    phrase boundary (a 32-beat boundary, with the defaults of
    ``phrase_beats=16`` and ``major_phrase_multiple=2``).

    Args:
        bpm: Track tempo, from ``<TEMPO BPM="...">``.
        grid_anchor_ms: Grid anchor (beat 0), in milliseconds -- the
            ``START`` attribute of the ``<CUE_V2 TYPE="4">`` (``AutoGrid``)
            element.
        duration_ms: Track duration, in milliseconds -- from
            ``<INFO PLAYTIME_FLOAT="..."> * 1000``.
        phrase_beats: Base phrase granularity, in beats (spec default: 16,
            a 4-bar block).
        major_phrase_multiple: Every Nth candidate is additionally tagged
            ``is_major_phrase=True`` (spec default: 2, i.e. every 32 beats).

    Returns:
        A chronologically ordered list of ``PhraseCandidate``. Empty if
        ``bpm`` or ``duration_ms`` is not strictly positive (spec section
        4.4, items 2-3) -- this is a valid, silent outcome, not an error.
    """
    if bpm <= 0 or duration_ms <= 0:
        return []

    length_ms = beat_length_ms(bpm)
    candidates: list[PhraseCandidate] = []
    n = 0
    while True:
        beat_index = n * phrase_beats
        t_ms = grid_anchor_ms + beat_index * length_ms
        if t_ms > duration_ms:
            break
        candidates.append(
            PhraseCandidate(
                beat_index=beat_index,
                time_ms=t_ms,
                is_major_phrase=(n % major_phrase_multiple == 0),
            )
        )
        n += 1
    return candidates
