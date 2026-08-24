"""Free-text ingest: the path that must never cost a model call."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.ingest.text_parse import parse_text
from src.ingest.units import MGDL, MMOL, to_mmol

NOW = datetime(2026, 8, 24, 12, 0)


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("сахар 8", 8.0, MMOL),
        ("Глюкоза 4,5 ммоль/л натощак", 4.5, MMOL),
        ("гк 130 mg/dl", 130.0, MGDL),
        ("сахар: 6.1", 6.1, MMOL),
        ("уровень сахара — 11", 11.0, MMOL),
        ("8.9 ммоль/л", 8.9, MMOL),
    ],
)
def test_glucose_extraction(text, value, unit):
    parsed = parse_text(text, now=NOW)
    assert parsed.glucose == [(value, unit)]


def test_mgdl_converted_to_mmol():
    parsed = parse_text("гк 130 mg/dl", now=NOW)
    value, unit = parsed.glucose[0]
    assert to_mmol(value, unit) == pytest.approx(7.21, abs=0.01)


def test_implausible_values_are_ignored():
    assert parse_text("сахар 900", now=NOW).glucose == []


def test_bare_number_without_keyword_is_not_glucose():
    assert parse_text("съел 2 яблока", now=NOW).glucose == []


def test_full_message_yields_every_field():
    parsed = parse_text(
        "вчера в 21:00 сахар 9.1, вес 72,3, самочувствие 3, выпила метформин 500 мг",
        now=NOW,
    )
    assert parsed.glucose == [(9.1, MMOL)]
    assert parsed.weight_kg == 72.3
    assert parsed.wellbeing == 3
    assert parsed.medications == [("метформин", "500 мг")]
    assert parsed.at == datetime(2026, 8, 23, 21, 0)


def test_decimal_value_is_not_read_as_a_date():
    """«сахар 9.1» must not parse as 9 January — the classic regex trap."""
    parsed = parse_text("вчера сахар 9.1 в 21:00", now=NOW)
    assert parsed.at.date() == datetime(2026, 8, 23).date()


def test_explicit_date_wins_over_time_dot_form():
    parsed = parse_text("23.08 в 9:15 сахар 6,1", now=NOW)
    assert parsed.at == datetime(2026, 8, 23, 9, 15)


def test_future_time_rolls_back_a_day():
    parsed = parse_text("в 23:40 сахар 7", now=datetime(2026, 8, 24, 0, 20))
    assert parsed.at == datetime(2026, 8, 23, 23, 40)


def test_leftover_is_the_meal_description():
    parsed = parse_text("съела овсянку с бананом в 9:00", now=NOW)
    assert parsed.is_empty
    assert parsed.leftover == "съела овсянку с бананом"


def test_fasting_flag():
    assert parse_text("глюкоза 5.1 натощак", now=NOW).fasting is True
