"""Pure Smart Playlist rule evaluation over Traktor NML track data."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Mapping

TrackData = Mapping[str, Any] | ET.Element
Rule = Mapping[str, Any]

_STRING_FIELDS = {"genre", "label", "comment"}
_NUMERIC_FIELDS = {"bpm", "playcount"}
_DATE_FIELDS = {"import_date", "last_played"}
_SUPPORTED_FIELDS = _STRING_FIELDS | _NUMERIC_FIELDS | _DATE_FIELDS | {"key", "rating"}
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
        if operator not in {"is_exactly", "equals"}:
            raise ValueError("key supports only is_exactly or equals")
        return _match_key(_text_value(track, field), rule.get("value"))
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


def _match_key(actual: str, value: Any) -> bool:
    """Match one normalized NML key against comma-separated exact targets."""
    targets = [
        target.strip().casefold()
        for target in _required_text(value).split(",")
        if target.strip()
    ]
    if not targets:
        raise ValueError("key value must contain at least one non-empty key")
    return actual.strip().casefold() in targets


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


def _date_value(track: TrackData, field: str) -> date | None:
    return _parse_date(_attribute(track, field))


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
