"""Pure Smart Playlist rule evaluation over Traktor NML track data."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Mapping

from cuegrid.nml.parser import TRAKTOR_MUSICAL_KEY_TO_OPEN_KEY, normalize_to_open_key

TrackData = Mapping[str, Any] | ET.Element
Rule = Mapping[str, Any]

_STRING_FIELDS = {"genre", "label", "comment"}
_NUMERIC_FIELDS = {"bpm", "playcount"}
_DATE_FIELDS = {"import_date", "last_played"}
_SUPPORTED_FIELDS = _STRING_FIELDS | _NUMERIC_FIELDS | _DATE_FIELDS | {"key", "rating", "track_format"}
_BPM_TOLERANCE = 0.5


def matches_rules(
    track: TrackData,
    rules: list[Rule],
    match: str = "all",
    *,
    today: date | None = None,
) -> bool:
    """Return whether ``track`` satisfies all or any validated Smart Playlist rules.

    ``today`` is injectable so calendar-date rules are deterministic in tests.
    The function accepts a raw NML ``ENTRY`` element or a mapping containing
    either canonical fields (for example ``{"bpm": 128}``) or NML-shaped
    ``INFO``/``TEMPO`` mappings.
    """
    if match not in {"all", "any"}:
        raise ValueError("match must be 'all' or 'any'")
    if not rules:
        raise ValueError("Smart Playlist requires at least one rule")

    results = [matches_rule(track, rule, today=today) for rule in rules]
    return all(results) if match == "all" else any(results)


def matches_rule(track: TrackData, rule: Rule, *, today: date | None = None) -> bool:
    """Return whether one validated rule matches a track.

    Missing numeric attributes evaluate as zero. Missing or malformed dates
    evaluate as no date and therefore never satisfy date predicates.
    """
    field = rule.get("field")
    operator = rule.get("operator")
    if field not in _SUPPORTED_FIELDS:
        raise ValueError(f"unsupported Smart Playlist field: {field!r}")
    if not isinstance(operator, str):
        raise ValueError("Smart Playlist rule operator must be a string")

    if field == "bpm":
        return _match_bpm(_numeric_value(track, field), operator, rule.get("value"))
    if field in _NUMERIC_FIELDS:
        return _match_numeric(_numeric_value(track, field), operator, rule.get("value"))
    if field in _STRING_FIELDS:
        return _match_string(_text_value(track, field), operator, rule.get("value"))
    if field == "key":
        if operator not in {
            "is_exactly",
            "equals",
            "contains",
            "does_not_contain",
            "is_harmonically_compatible",
            "is_harmonically_compatible_fuzzy",
        }:
            raise ValueError("unsupported key operator: %r" % operator)
        return _match_key(_key_value(track), operator, rule.get("value"))
    if field == "track_format":
        return _match_track_format(track, operator, rule.get("value"))
    if field in _DATE_FIELDS:
        return _match_date(_date_value(track, field), operator, rule.get("value"), today or date.today())
    return _match_rating(_numeric_value(track, "rating"), operator, rule.get("value"))


def _match_numeric(actual: float, operator: str, value: Any) -> bool:
    if operator == "between":
        if not isinstance(value, Mapping):
            raise ValueError("between requires an object with min and max")
        minimum = _finite_number(value.get("min"))
        maximum = _finite_number(value.get("max"))
        if minimum > maximum:
            raise ValueError("between min must not exceed max")
        return minimum <= actual <= maximum

    expected = _finite_number(value)
    if operator == "equals":
        return actual == expected
    if operator == "greater_than":
        return actual > expected
    if operator == "less_than":
        return actual < expected
    raise ValueError(f"unsupported numeric operator: {operator!r}")


def _match_bpm(actual: float, operator: str, value: Any) -> bool:
    """Match BPM with Traktor-safe tolerance and equals-only tempo aliases."""
    if operator == "between":
        if not isinstance(value, Mapping):
            raise ValueError("between requires an object with min and max")
        minimum = _finite_number(value.get("min"))
        maximum = _finite_number(value.get("max"))
        if minimum > maximum:
            raise ValueError("between min must not exceed max")
        return minimum - _BPM_TOLERANCE < actual < maximum + _BPM_TOLERANCE

    expected = _finite_number(value)
    if operator == "equals":
        return any(
            abs(candidate - expected) < _BPM_TOLERANCE
            for candidate in (actual, actual * 2, actual / 2)
        )
    if operator == "greater_than":
        return actual - expected >= _BPM_TOLERANCE
    if operator == "less_than":
        return expected - actual >= _BPM_TOLERANCE
    raise ValueError(f"unsupported numeric operator: {operator!r}")


def _match_string(actual: str, operator: str, value: Any) -> bool:
    expected = _required_text(value).casefold()
    actual = actual.casefold()
    if operator == "contains":
        return expected in actual
    if operator == "is_exactly":
        return actual == expected
    if operator == "does_not_contain":
        return expected not in actual
    raise ValueError(f"unsupported string operator: {operator!r}")


def _match_key(actual: str, operator: str, value: Any) -> bool:
    """Evaluate a key rule after resolving both operands to Open Key."""
    targets = [
        normalize_to_open_key(target)
        for target in _required_text(value).split(",")
        if target.strip()
    ]
    if not targets or any(not target for target in targets):
        raise ValueError("key values must use valid Open Key notation")

    normalized_actual = normalize_to_open_key(actual)
    if operator in {"is_harmonically_compatible", "is_harmonically_compatible_fuzzy"}:
        valid_keys: set[str] = set()
        include_adjacent = operator == "is_harmonically_compatible_fuzzy"
        for target in targets:
            direct_matches, adjacent_matches = _harmonic_matches(target)
            valid_keys.update(direct_matches)
            if include_adjacent:
                valid_keys.update(adjacent_matches)
        return normalized_actual in valid_keys

    matches = normalized_actual in targets
    return not matches if operator == "does_not_contain" else matches


def _harmonic_matches(key: str) -> tuple[list[str], list[str]]:
    """Return direct and +/-1-semitone Open Key compatibility matches."""
    number = int(key[:-1])
    mode = key[-1]

    def wrap(value: int) -> int:
        return (value - 1) % 12 + 1

    def toggle_mode(value: str) -> str:
        return "d" if value == "m" else "m"

    def direct(value: int) -> list[str]:
        return [
            f"{value}{mode}",
            f"{wrap(value + 1)}{mode}",
            f"{wrap(value - 1)}{mode}",
            f"{value}{toggle_mode(mode)}",
        ]

    direct_matches = direct(number)
    direct_set = set(direct_matches)
    adjacent_matches = [
        candidate
        for candidate in [*direct(wrap(number + 7)), *direct(wrap(number + 5))]
        if candidate not in direct_set
    ]
    return direct_matches, list(dict.fromkeys(adjacent_matches))


def _match_track_format(track: TrackData, operator: str, value: Any) -> bool:
    """Match Traktor native-Stem availability from the INFO FLAGS bitmask."""
    if operator != "is_exactly":
        raise ValueError(f"unsupported track format operator: {operator!r}")
    if _required_text(value).casefold() != "stem":
        raise ValueError("track_format must be exactly 'Stem'")
    return _flags_value(track) & 0x40 == 0x40


def _match_date(actual: date | None, operator: str, value: Any, today: date) -> bool:
    if actual is None:
        return False
    if operator == "in_last_days":
        days = _positive_integer(value)
        return today - timedelta(days=days) <= actual <= today
    expected = _parse_date(value)
    if expected is None:
        raise ValueError("date value must use YYYY/M/D or YYYY/MM/DD")
    if operator == "before":
        return actual < expected
    if operator == "after":
        return actual > expected
    raise ValueError(f"unsupported date operator: {operator!r}")


def _match_rating(actual_ranking: float, operator: str, value: Any) -> bool:
    stars = _positive_integer(value)
    if not 1 <= stars <= 5:
        raise ValueError("rating must be an integer from 1 through 5")
    expected_ranking = stars * 51
    if operator == "equals":
        return actual_ranking == expected_ranking
    if operator == "greater_than_or_equal":
        return actual_ranking >= expected_ranking
    if operator == "less_than_or_equal":
        return actual_ranking <= expected_ranking
    raise ValueError(f"unsupported rating operator: {operator!r}")


def _numeric_value(track: TrackData, field: str) -> float:
    raw = _attribute(track, field)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _text_value(track: TrackData, field: str) -> str:
    raw = _attribute(track, field)
    return raw.strip() if isinstance(raw, str) else ""


def _key_value(track: TrackData) -> str:
    """Resolve a track key with the same native-first policy as ``NmlParser``."""
    if isinstance(track, ET.Element):
        musical_key_el = track.find("MUSICAL_KEY")
        if musical_key_el is not None:
            try:
                resolved = TRAKTOR_MUSICAL_KEY_TO_OPEN_KEY.get(
                    int(musical_key_el.get("VALUE", "")), ""
                )
            except ValueError:
                resolved = ""
            if resolved:
                return resolved
        info_el = track.find("INFO")
        return normalize_to_open_key(info_el.get("KEY", "") if info_el is not None else "")
    return normalize_to_open_key(_text_value(track, "key"))


def _date_value(track: TrackData, field: str) -> date | None:
    return _parse_date(_attribute(track, field))


def _flags_value(track: TrackData) -> int:
    """Return a valid INFO FLAGS bitmask, defaulting malformed values to zero."""
    raw = _attribute(track, "flags")
    if isinstance(raw, bool):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _attribute(track: TrackData, field: str) -> Any:
    nml_attributes = {
        "bpm": ("TEMPO", "BPM"),
        "playcount": ("INFO", "PLAYCOUNT"),
        "genre": ("INFO", "GENRE"),
        "label": ("INFO", "LABEL"),
        "comment": ("INFO", "COMMENT"),
        "key": ("INFO", "KEY"),
        "import_date": ("INFO", "IMPORT_DATE"),
        "last_played": ("INFO", "LAST_PLAYED"),
        "rating": ("INFO", "RANKING"),
        "flags": ("INFO", "FLAGS"),
    }
    if isinstance(track, ET.Element):
        child_name, attribute = nml_attributes[field]
        child = track.find(child_name)
        return child.get(attribute) if child is not None else None

    if field in track:
        return track[field]
    child_name, attribute = nml_attributes[field]
    child = track.get(child_name)
    if isinstance(child, Mapping):
        return child.get(attribute)
    return None


def _finite_number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("numeric value must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric value must be finite") from exc
    if not math.isfinite(number):
        raise ValueError("numeric value must be finite")
    return number


def _positive_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("value must be a positive integer")
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be a positive integer") from exc
    if integer <= 0 or str(integer) != str(value).strip():
        raise ValueError("value must be a positive integer")
    return integer


def _required_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text value must be a non-empty string")
    return value.strip()


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        year, month, day = (int(part) for part in value.split("/"))
        return date(year, month, day)
    except (TypeError, ValueError):
        return None
