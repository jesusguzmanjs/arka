"""Unit tests for the pure Smart Playlist rule evaluator."""

from datetime import date
import xml.etree.ElementTree as ET

import pytest

from cuegrid.core.smart_playlist import matches_rule, matches_rules


@pytest.fixture()
def track() -> dict:
    return {
        "bpm": 128.5,
        "playcount": 3,
        "genre": "Deep Techno",
        "label": "Example Records",
        "comment": "Peak-time tool",
        "key": "8A",
        "import_date": "2026/7/15",
        "last_played": "2026/7/20",
        "rating": 204,
    }


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ({"field": "bpm", "operator": "equals", "value": 128.5}, True),
        ({"field": "bpm", "operator": "greater_than", "value": 128}, True),
        ({"field": "bpm", "operator": "less_than", "value": 128}, False),
        ({"field": "bpm", "operator": "between", "value": {"min": 128, "max": 129}}, True),
        ({"field": "playcount", "operator": "equals", "value": 3}, True),
        ({"field": "playcount", "operator": "greater_than", "value": 2}, True),
        ({"field": "playcount", "operator": "less_than", "value": 3}, False),
        ({"field": "playcount", "operator": "between", "value": {"min": 1, "max": 3}}, True),
        ({"field": "genre", "operator": "contains", "value": "TECH"}, True),
        ({"field": "label", "operator": "is_exactly", "value": "example records"}, True),
        ({"field": "comment", "operator": "does_not_contain", "value": "warmup"}, True),
        ({"field": "key", "operator": "is_exactly", "value": "8a"}, True),
        ({"field": "rating", "operator": "equals", "value": 4}, True),
        ({"field": "rating", "operator": "greater_than_or_equal", "value": 4}, True),
        ({"field": "rating", "operator": "less_than_or_equal", "value": 3}, False),
    ],
)
def test_matches_supported_numeric_text_key_and_rating_rules(track, rule, expected):
    assert matches_rule(track, rule) is expected


def test_matches_date_rules_against_injected_today(track):
    today = date(2026, 7, 21)
    assert matches_rule(
        track, {"field": "import_date", "operator": "in_last_days", "value": 7}, today=today
    )
    assert matches_rule(
        track, {"field": "last_played", "operator": "before", "value": "2026/7/21"}, today=today
    )
    assert matches_rule(
        track, {"field": "last_played", "operator": "after", "value": "2026/7/19"}, today=today
    )


def test_bpm_equals_uses_traktor_float_tolerance_and_half_double_tempo():
    equals_120 = {"field": "bpm", "operator": "equals", "value": 120}
    equals_140 = {"field": "bpm", "operator": "equals", "value": 140}

    assert matches_rule({"bpm": 119.996}, equals_120)
    assert matches_rule({"bpm": 70.0}, equals_140)


def test_bpm_bounds_apply_the_same_float_tolerance():
    track = {"bpm": 119.996}

    assert not matches_rule(track, {"field": "bpm", "operator": "greater_than", "value": 120})
    assert not matches_rule(track, {"field": "bpm", "operator": "less_than", "value": 120})
    assert matches_rule(track, {"field": "bpm", "operator": "between", "value": {"min": 120, "max": 120}})


def test_key_matches_any_trimmed_case_insensitive_comma_separated_value():
    assert matches_rule(
        {"key": "8A"},
        {"field": "key", "operator": "is_exactly", "value": " 8a, 8b, 9a "},
    )
    assert matches_rule(
        {"key": "8A"},
        {"field": "key", "operator": "equals", "value": "8b, 8a"},
    )


def test_missing_attributes_default_to_zero_or_no_date_and_xml_elements_are_supported():
    entry = ET.fromstring('<ENTRY><INFO GENRE="Techno" /><TEMPO BPM="130" /></ENTRY>')

    assert matches_rule(entry, {"field": "playcount", "operator": "equals", "value": 0})
    assert matches_rule(entry, {"field": "rating", "operator": "less_than_or_equal", "value": 1})
    assert matches_rule(entry, {"field": "genre", "operator": "contains", "value": "tech"})
    assert not matches_rule(
        entry,
        {"field": "last_played", "operator": "in_last_days", "value": 30},
        today=date(2026, 7, 21),
    )


def test_all_and_any_global_conditions(track):
    rules = [
        {"field": "genre", "operator": "contains", "value": "techno"},
        {"field": "playcount", "operator": "greater_than", "value": 9},
    ]

    assert not matches_rules(track, rules, "all")
    assert matches_rules(track, rules, "any")


@pytest.mark.parametrize(
    "rules, match",
    [
        ([], "all"),
        ([{"field": "bpm", "operator": "unknown", "value": 120}], "all"),
        ([{"field": "rating", "operator": "equals", "value": 6}], "all"),
        ([{"field": "bpm", "operator": "between", "value": {"min": 130, "max": 120}}], "all"),
        ([{"field": "bpm", "operator": "equals", "value": 120}], "invalid"),
    ],
)
def test_rejects_invalid_rule_contracts(track, rules, match):
    with pytest.raises(ValueError):
        matches_rules(track, rules, match)
