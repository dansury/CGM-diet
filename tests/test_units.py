"""Glucose unit conversion and plausibility."""

from __future__ import annotations

import pytest

from src.ingest import units


def test_round_trip_mgdl():
    assert units.to_mgdl(units.to_mmol(126, units.MGDL)) == pytest.approx(126, abs=0.5)


def test_canonical_unit_accepts_russian_spellings():
    assert units.canonical_unit("ммоль/л") == units.MMOL
    assert units.canonical_unit("мг/дл") == units.MGDL
    assert units.canonical_unit(None) == units.MMOL


def test_guess_unit_uses_magnitude():
    assert units.guess_unit(7.8) == units.MMOL
    assert units.guess_unit(140) == units.MGDL


def test_overlap_resolves_to_mmol():
    """18–33 is valid in both scales; we prefer mmol/L and let the user flip it."""
    assert units.guess_unit(25) == units.MMOL


def test_plausibility_bounds():
    assert units.is_plausible(5.5, units.MMOL)
    assert not units.is_plausible(0.4, units.MMOL)
    assert units.is_plausible(180, units.MGDL)
    assert not units.is_plausible(900, units.MGDL)


def test_formatting_follows_the_users_unit():
    assert units.format_value(7.2, units.MMOL) == "7.2 ммоль/л"
    assert units.format_value(7.2, units.MGDL) == "130 мг/дл"
    assert units.format_delta(-1.24, units.MMOL) == "−1.2 ммоль/л"
    assert units.format_delta(1.0, units.MMOL) == "+1.0 ммоль/л"
