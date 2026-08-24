"""Standard CGM variability metrics, computed from the user's own series.

Formulas are the published clinical definitions (ADA/ATTD consensus and the
original papers); the reference implementation this was checked against is the
MIT-licensed R package `cgmquantify`. Everything here is descriptive — the bot
shows numbers and ranges, never a diagnosis.

Metric inputs are mmol/L (the storage unit). Metrics that are *defined* in
mg/dL (GMI, eA1c, LBGI/HBGI, J-index) convert internally.

References
----------
* TIR/TAR/TBR targets: Battelino et al., Diabetes Care 2019 (3.9–10.0 mmol/L).
* GMI: Bergenstal et al., Diabetes Care 2018 — GMI(%) = 3.31 + 0.02392 × mean(mg/dL).
* eA1c: Nathan et al., Diabetes Care 2008 — (46.7 + mean mg/dL) / 28.7.
* LBGI/HBGI: Kovatchev et al., Diabetes Care 1998.
* J-index: Wojcicki, Horm Metab Res 1995.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from statistics import mean as _mean
from statistics import stdev as _stdev

from src.analytics.windows import GlucosePoint
from src.ingest.units import to_mgdl

TIR_LOW = 3.9
TIR_HIGH = 10.0
TBR_L2 = 3.0
TAR_L2 = 13.9
# A gap longer than this is treated as sensor downtime, not a flat line.
MAX_GAP_MIN = 30


@dataclass(slots=True)
class CGMSummary:
    n: int
    days: float
    mean: float
    sd: float | None
    cv: float | None
    tir: float | None
    tbr: float | None
    tbr_l2: float | None
    tar: float | None
    tar_l2: float | None
    gmi: float | None
    ea1c: float | None
    lbgi: float | None
    hbgi: float | None
    j_index: float | None
    mage: float | None


def _weighted_time(points: list[GlucosePoint]) -> list[tuple[GlucosePoint, float]]:
    """Attach a minute weight to each point (midpoint rule, gaps clipped).

    Sampling is irregular — screenshots, manual entries and CGM exports mix —
    so a plain point count would over-weight bursts of manual measurements.
    """
    ordered = sorted(points, key=lambda p: p.at)
    out: list[tuple[GlucosePoint, float]] = []
    for idx, point in enumerate(ordered):
        prev_gap = (
            (point.at - ordered[idx - 1].at).total_seconds() / 60.0 if idx > 0 else MAX_GAP_MIN
        )
        next_gap = (
            (ordered[idx + 1].at - point.at).total_seconds() / 60.0
            if idx + 1 < len(ordered)
            else MAX_GAP_MIN
        )
        weight = min(prev_gap, MAX_GAP_MIN) / 2.0 + min(next_gap, MAX_GAP_MIN) / 2.0
        out.append((point, max(weight, 0.0)))
    return out


def percent_in(points: list[GlucosePoint], low: float | None, high: float | None) -> float | None:
    """Time-weighted share of readings inside [low, high], in percent."""
    weighted = _weighted_time(points)
    total = sum(w for _, w in weighted)
    if total <= 0:
        return None
    inside = sum(
        w
        for p, w in weighted
        if (low is None or p.value >= low) and (high is None or p.value <= high)
    )
    return round(inside / total * 100.0, 1)


def cv(points: list[GlucosePoint]) -> float | None:
    values = [p.value for p in points]
    if len(values) < 2:
        return None
    avg = _mean(values)
    if avg == 0:
        return None
    return round(_stdev(values) / avg * 100.0, 1)


def gmi(points: list[GlucosePoint]) -> float | None:
    if not points:
        return None
    return round(3.31 + 0.02392 * to_mgdl(_mean([p.value for p in points])), 2)


def ea1c(points: list[GlucosePoint]) -> float | None:
    if not points:
        return None
    return round((46.7 + to_mgdl(_mean([p.value for p in points]))) / 28.7, 2)


def j_index(points: list[GlucosePoint]) -> float | None:
    if len(points) < 2:
        return None
    mgdl = [to_mgdl(p.value) for p in points]
    return round(0.001 * (_mean(mgdl) + _stdev(mgdl)) ** 2, 2)


def _bg_risk(value_mmol: float) -> float:
    """Kovatchev's symmetrised risk transform of a mg/dL reading."""
    mgdl = max(to_mgdl(value_mmol), 1.0)
    return 1.509 * (math.log(mgdl) ** 1.084 - 5.381)


def lbgi_hbgi(points: list[GlucosePoint]) -> tuple[float | None, float | None]:
    """Low / High Blood Glucose Index — asymmetric risk of hypo vs hyper."""
    if not points:
        return None, None
    low_terms: list[float] = []
    high_terms: list[float] = []
    for point in points:
        f = _bg_risk(point.value)
        risk = 10.0 * f * f
        low_terms.append(risk if f < 0 else 0.0)
        high_terms.append(risk if f > 0 else 0.0)
    return round(_mean(low_terms), 2), round(_mean(high_terms), 2)


def mage(points: list[GlucosePoint]) -> float | None:
    """Mean Amplitude of Glycemic Excursions (Service 1970), simple form.

    Turning points are found on the raw series; excursions smaller than one
    standard deviation are discarded, and the surviving amplitudes averaged.
    """
    ordered = sorted(points, key=lambda p: p.at)
    values = [p.value for p in ordered]
    if len(values) < 5:
        return None
    threshold = _stdev(values)
    if threshold == 0:
        return None
    extrema = [values[0]]
    for prev, cur, nxt in zip(values, values[1:], values[2:], strict=False):
        if (cur - prev) * (nxt - cur) < 0:
            extrema.append(cur)
    extrema.append(values[-1])
    amplitudes = [
        abs(b - a) for a, b in zip(extrema, extrema[1:], strict=False) if abs(b - a) > threshold
    ]
    if not amplitudes:
        return None
    return round(_mean(amplitudes), 2)


def summarize(points: list[GlucosePoint]) -> CGMSummary:
    """Everything `/stats` shows about the glucose series itself."""
    ordered = sorted(points, key=lambda p: p.at)
    values = [p.value for p in ordered]
    if not values:
        return CGMSummary(0, 0.0, 0.0, *([None] * 13))
    span: timedelta = ordered[-1].at - ordered[0].at
    low, high = lbgi_hbgi(ordered)
    return CGMSummary(
        n=len(values),
        days=round(span.total_seconds() / 86400.0, 2),
        mean=round(_mean(values), 2),
        sd=round(_stdev(values), 2) if len(values) > 1 else None,
        cv=cv(ordered),
        tir=percent_in(ordered, TIR_LOW, TIR_HIGH),
        tbr=percent_in(ordered, None, TIR_LOW - 0.01),
        tbr_l2=percent_in(ordered, None, TBR_L2 - 0.01),
        tar=percent_in(ordered, TIR_HIGH + 0.01, None),
        tar_l2=percent_in(ordered, TAR_L2, None),
        gmi=gmi(ordered),
        ea1c=ea1c(ordered),
        lbgi=low,
        hbgi=high,
        j_index=j_index(ordered),
        mage=mage(ordered),
    )


__all__ = [
    "CGMSummary",
    "TAR_L2",
    "TBR_L2",
    "TIR_HIGH",
    "TIR_LOW",
    "cv",
    "ea1c",
    "gmi",
    "j_index",
    "lbgi_hbgi",
    "mage",
    "percent_in",
    "summarize",
]
