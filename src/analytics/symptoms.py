"""Link how the user feels to what the glucose curve was doing.

Сонливость / потливость / «туман в голове» can come from a postprandial spike,
from a hypo, or from something the bot knows nothing about (постменопауза,
недосып, инфекция). The honest output is therefore a *contrast*: what the
glucose looked like when the symptom was reported, versus all other check-ins.
The bot never attributes a symptom to a cause. See `spec/wellbeing.md`.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.analytics.stats import MIN_OBSERVATIONS, grade_confidence, mann_whitney_p, mean_ci
from src.analytics.windows import GlucosePoint

# How far a glucose reading may be from a check-in to describe it.
MATCH_WINDOW_MIN = 30
# A check-in this long after a meal is treated as postprandial.
POSTPRANDIAL_MIN = 150


@dataclass(slots=True)
class CheckinLike:
    at: datetime
    score: int
    symptoms: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CheckinContext:
    at: datetime
    score: int
    symptoms: list[str]
    glucose: float | None = None
    minutes_since_meal: int | None = None
    meal_tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SymptomStats:
    symptom: str
    n: int
    mean_glucose: float | None
    median_glucose: float | None
    mean_without: float | None
    contrast: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    confidence: str
    share_postprandial: float | None
    n_low_glucose: int = 0


def nearest_glucose(
    points: list[GlucosePoint], at: datetime, *, window_min: int = MATCH_WINDOW_MIN
) -> float | None:
    if not points:
        return None
    ordered = sorted(points, key=lambda p: p.at)
    times = [p.at for p in ordered]
    idx = bisect.bisect_left(times, at)
    best: GlucosePoint | None = None
    for candidate in ordered[max(0, idx - 1) : idx + 1]:
        if best is None or abs(candidate.at - at) < abs(best.at - at):
            best = candidate
    if best is None or abs(best.at - at) > timedelta(minutes=window_min):
        return None
    return best.value


def build_context(
    checkins: list[CheckinLike],
    points: list[GlucosePoint],
    meals: list[tuple[datetime, list[str]]] | None = None,
) -> list[CheckinContext]:
    """Attach the nearest glucose value and the preceding meal to each check-in."""
    meal_list = sorted(meals or [], key=lambda m: m[0])
    meal_times = [m[0] for m in meal_list]
    out: list[CheckinContext] = []
    for checkin in checkins:
        ctx = CheckinContext(
            at=checkin.at,
            score=checkin.score,
            symptoms=list(checkin.symptoms),
            glucose=nearest_glucose(points, checkin.at),
        )
        idx = bisect.bisect_right(meal_times, checkin.at) - 1
        if idx >= 0:
            gap = int((checkin.at - meal_times[idx]).total_seconds() // 60)
            if 0 <= gap <= POSTPRANDIAL_MIN:
                ctx.minutes_since_meal = gap
                ctx.meal_tags = list(meal_list[idx][1])
        out.append(ctx)
    return out


def aggregate_symptoms(
    contexts: list[CheckinContext], *, min_observations: int = MIN_OBSERVATIONS
) -> list[SymptomStats]:
    """Per-symptom contrast of glucose at the check-in vs. all other check-ins."""
    with_glucose = [c for c in contexts if c.glucose is not None]
    results: list[SymptomStats] = []
    all_symptoms = {s for c in contexts for s in c.symptoms}
    for symptom in sorted(all_symptoms):
        present = [c for c in with_glucose if symptom in c.symptoms]
        absent = [c for c in with_glucose if symptom not in c.symptoms]
        total_present = [c for c in contexts if symptom in c.symptoms]
        if len(total_present) < min_observations:
            continue
        values = [c.glucose for c in present if c.glucose is not None]
        others = [c.glucose for c in absent if c.glucose is not None]
        if values:
            mean, _sd, ci_low, ci_high = mean_ci(values)
            median = sorted(values)[len(values) // 2]
        else:
            mean = median = ci_low = ci_high = None
        p_value = mann_whitney_p(values, others) if values and others else None
        mean_without = round(sum(others) / len(others), 2) if others else None
        postprandial = [c for c in total_present if c.minutes_since_meal is not None]
        results.append(
            SymptomStats(
                symptom=symptom,
                n=len(total_present),
                mean_glucose=mean,
                median_glucose=median,
                mean_without=mean_without,
                contrast=(
                    round(mean - mean_without, 2)
                    if mean is not None and mean_without is not None
                    else None
                ),
                ci_low=ci_low,
                ci_high=ci_high,
                p_value=p_value,
                confidence=grade_confidence(
                    n=len(values),
                    ci_low=None,
                    mean_delta=abs((mean or 0) - (mean_without or 0)),
                    p_value=p_value,
                ),
                share_postprandial=(
                    round(len(postprandial) / len(total_present) * 100, 1)
                    if total_present
                    else None
                ),
                n_low_glucose=sum(1 for v in values if v < 3.9),
            )
        )
    results.sort(key=lambda s: (-(s.contrast or 0), -s.n))
    return results


def score_series(contexts: list[CheckinContext]) -> list[tuple[datetime, int]]:
    """Wellbeing score over time — the data behind the «график сонливости»."""
    return [(c.at, c.score) for c in sorted(contexts, key=lambda c: c.at)]


def symptom_series(contexts: list[CheckinContext], symptom: str) -> list[tuple[datetime, int]]:
    return [
        (c.at, 1 if symptom in c.symptoms else 0) for c in sorted(contexts, key=lambda c: c.at)
    ]


__all__ = [
    "CheckinContext",
    "CheckinLike",
    "MATCH_WINDOW_MIN",
    "POSTPRANDIAL_MIN",
    "SymptomStats",
    "aggregate_symptoms",
    "build_context",
    "nearest_glucose",
    "score_series",
    "symptom_series",
]
