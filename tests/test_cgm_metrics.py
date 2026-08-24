"""CGM variability metrics against hand-checked references."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analytics import cgm_metrics as m
from src.analytics.windows import GlucosePoint
from src.ingest.units import to_mgdl

T0 = datetime(2026, 8, 24, 0, 0)


def flat(value: float, count: int = 288) -> list[GlucosePoint]:
    return [GlucosePoint(at=T0 + timedelta(minutes=5 * i), value=value) for i in range(count)]


def test_gmi_matches_the_published_formula():
    points = flat(7.0)
    assert m.gmi(points) == pytest.approx(3.31 + 0.02392 * to_mgdl(7.0), abs=0.01)


def test_ea1c_matches_the_published_formula():
    points = flat(7.0)
    assert m.ea1c(points) == pytest.approx((46.7 + to_mgdl(7.0)) / 28.7, abs=0.01)


def test_time_in_range_is_full_for_a_flat_in_range_series():
    assert m.percent_in(flat(6.0), m.TIR_LOW, m.TIR_HIGH) == 100.0


def test_time_in_range_is_zero_above_the_band():
    assert m.percent_in(flat(12.0), m.TIR_LOW, m.TIR_HIGH) == 0.0


def test_time_weighting_ignores_bursts_of_manual_entries():
    """Three readings a minute apart must not outweigh a whole quiet day."""
    burst = [GlucosePoint(at=T0 + timedelta(minutes=i), value=15.0) for i in range(3)]
    quiet = [GlucosePoint(at=T0 + timedelta(hours=2 + i), value=6.0) for i in range(12)]
    tir = m.percent_in(burst + quiet, m.TIR_LOW, m.TIR_HIGH)
    assert tir > 80.0


def test_cv_is_zero_for_a_flat_series():
    assert m.cv(flat(6.0)) == 0.0


def test_lbgi_is_high_for_hypoglycaemia_and_hbgi_for_hyper():
    low, high = m.lbgi_hbgi(flat(3.0))
    assert low > high
    low, high = m.lbgi_hbgi(flat(15.0))
    assert high > low


def test_mage_needs_variability():
    assert m.mage(flat(6.0)) is None


def test_summarize_on_an_empty_series():
    summary = m.summarize([])
    assert summary.n == 0 and summary.tir is None


def test_summarize_reports_every_field():
    points = [
        GlucosePoint(at=T0 + timedelta(minutes=15 * i), value=5.0 + 4.0 * (i % 7) / 6)
        for i in range(96)
    ]
    summary = m.summarize(points)
    assert summary.n == 96
    assert 0 <= summary.tir <= 100
    assert summary.gmi and summary.ea1c and summary.j_index
