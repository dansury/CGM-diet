"""What physical activity does to the same meal.

Samsung Health (via Health Connect) gives steps in 15-minute buckets. For each
meal we sum the steps in the hour after it and split the excursions into
“походил” vs “сидел”, then compare the mean rise. Same wording rule as
everywhere else: association, not causation. See `spec/health_sync.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.analytics.stats import MIN_OBSERVATIONS, mann_whitney_p, mean_ci
from src.analytics.windows import Excursion, MealLike

# Steps in the hour after a meal that count as “прогулялся”.
ACTIVE_STEPS_THRESHOLD = 1000
POST_MEAL_ACTIVITY_MIN = 60


@dataclass(slots=True)
class ActivityBucket:
    start_at: datetime
    end_at: datetime
    steps: int


@dataclass(slots=True)
class ActivityContrast:
    n_active: int
    n_sedentary: int
    mean_active: float | None
    mean_sedentary: float | None
    difference: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    threshold: int = ACTIVE_STEPS_THRESHOLD

    @property
    def meaningful(self) -> bool:
        return (
            self.n_active >= MIN_OBSERVATIONS
            and self.n_sedentary >= MIN_OBSERVATIONS
            and self.difference is not None
        )


def steps_after(
    buckets: list[ActivityBucket], meal_at: datetime, *, minutes: int = POST_MEAL_ACTIVITY_MIN
) -> int:
    """Steps overlapping [meal, meal+minutes], prorated over partial buckets."""
    window_end = meal_at + timedelta(minutes=minutes)
    total = 0.0
    for bucket in buckets:
        overlap_start = max(bucket.start_at, meal_at)
        overlap_end = min(bucket.end_at, window_end)
        overlap = (overlap_end - overlap_start).total_seconds()
        if overlap <= 0:
            continue
        span = (bucket.end_at - bucket.start_at).total_seconds()
        total += bucket.steps * (overlap / span if span > 0 else 1.0)
    return int(round(total))


def contrast_by_activity(
    meals: list[MealLike],
    excursions: list[Excursion],
    buckets: list[ActivityBucket],
    *,
    threshold: int = ACTIVE_STEPS_THRESHOLD,
) -> ActivityContrast:
    """Mean rise after meals followed by a walk vs. meals followed by sitting."""
    by_id = {m.id: m for m in meals}
    active: list[float] = []
    sedentary: list[float] = []
    for ex in excursions:
        meal = by_id.get(ex.meal_id)
        if meal is None or not ex.usable or ex.delta is None:
            continue
        (active if steps_after(buckets, meal.eaten_at) >= threshold else sedentary).append(ex.delta)

    mean_active = round(sum(active) / len(active), 2) if active else None
    mean_sedentary = round(sum(sedentary) / len(sedentary), 2) if sedentary else None
    difference = (
        round(mean_sedentary - mean_active, 2)
        if mean_active is not None and mean_sedentary is not None
        else None
    )
    ci_low = ci_high = None
    if len(active) >= 2:
        _m, _sd, ci_low, ci_high = mean_ci(active)
    return ActivityContrast(
        n_active=len(active),
        n_sedentary=len(sedentary),
        mean_active=mean_active,
        mean_sedentary=mean_sedentary,
        difference=difference,
        ci_low=ci_low,
        ci_high=ci_high,
        p_value=mann_whitney_p(active, sedentary),
        threshold=threshold,
    )


def daily_steps(buckets: list[ActivityBucket]) -> dict[str, int]:
    out: dict[str, int] = {}
    for bucket in buckets:
        key = bucket.start_at.date().isoformat()
        out[key] = out.get(key, 0) + bucket.steps
    return out


__all__ = [
    "ACTIVE_STEPS_THRESHOLD",
    "POST_MEAL_ACTIVITY_MIN",
    "ActivityBucket",
    "ActivityContrast",
    "contrast_by_activity",
    "daily_steps",
    "steps_after",
]
