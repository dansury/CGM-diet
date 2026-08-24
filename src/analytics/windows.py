"""Pair meals with the glucose curve around them.

For every meal we compute a *postprandial excursion*: a baseline just before
the meal, the peak inside a configurable window after it, and the incremental
area under the curve. Two windows are used by default (`spec/analytics.md`):

* `1h` — 45–90 min after the meal, matching a “через час” fingerstick;
* `2h`  — 90–150 min, matching a “через два часа” fingerstick.

A wide window is deliberate: CGM points land on a 5–15 min grid and people do
not measure exactly on the hour, so a point anywhere in the window counts.

Nothing here claims causality. `delta` is “насколько поднялся сахар после”,
never “насколько поднял сахар продукт”.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta

DEFAULT_WINDOW_1H = (45, 90)
DEFAULT_WINDOW_2H = (90, 150)
DEFAULT_BASELINE_WINDOW = 20
# Another meal starting inside the window contaminates the excursion.
CONTAMINATION_GUARD_MIN = 30


@dataclass(frozen=True, slots=True)
class GlucosePoint:
    at: datetime
    value: float  # mmol/L


@dataclass(slots=True)
class Excursion:
    """One meal's postprandial response in one window."""

    meal_id: int | None
    eaten_at: datetime
    window: str
    baseline: float | None = None
    peak: float | None = None
    peak_at: datetime | None = None
    delta: float | None = None
    iauc: float | None = None  # mmol/L × min above baseline
    n_points: int = 0
    contaminated: bool = False

    @property
    def usable(self) -> bool:
        return self.delta is not None and not self.contaminated


@dataclass(slots=True)
class MealLike:
    """Minimal meal shape the window math needs (keeps ORM out of this module)."""

    id: int | None
    eaten_at: datetime
    tags: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    carbs_g: float | None = None


def _series(points: list[GlucosePoint]) -> tuple[list[datetime], list[GlucosePoint]]:
    ordered = sorted(points, key=lambda p: p.at)
    return [p.at for p in ordered], ordered


def _slice(
    times: list[datetime], ordered: list[GlucosePoint], start: datetime, end: datetime
) -> list[GlucosePoint]:
    left = bisect.bisect_left(times, start)
    right = bisect.bisect_right(times, end)
    return ordered[left:right]


def compute_baseline(
    points: list[GlucosePoint],
    eaten_at: datetime,
    *,
    baseline_window: int = DEFAULT_BASELINE_WINDOW,
) -> float | None:
    """Mean of the readings just before the meal.

    Falls back to the single nearest reading within ±30 min — a fingerstick
    user has one pre-meal value, not a window full of them.
    """
    times, ordered = _series(points)
    if not ordered:
        return None
    window = _slice(
        times, ordered, eaten_at - timedelta(minutes=baseline_window), eaten_at + timedelta(minutes=10)
    )
    if window:
        return round(sum(p.value for p in window) / len(window), 2)
    nearest = min(ordered, key=lambda p: abs((p.at - eaten_at).total_seconds()))
    if abs((nearest.at - eaten_at).total_seconds()) <= 30 * 60:
        return round(nearest.value, 2)
    return None


def compute_excursion(
    meal: MealLike,
    points: list[GlucosePoint],
    *,
    window: tuple[int, int],
    window_name: str = "1h",
    baseline_window: int = DEFAULT_BASELINE_WINDOW,
    other_meals: list[datetime] | None = None,
) -> Excursion:
    """Baseline / peak / delta / iAUC for one meal in one window."""
    times, ordered = _series(points)
    result = Excursion(meal_id=meal.id, eaten_at=meal.eaten_at, window=window_name)
    baseline = compute_baseline(ordered, meal.eaten_at, baseline_window=baseline_window)
    result.baseline = baseline

    start = meal.eaten_at + timedelta(minutes=window[0])
    end = meal.eaten_at + timedelta(minutes=window[1])
    inside = _slice(times, ordered, start, end)
    result.n_points = len(inside)
    if baseline is None or not inside:
        return result

    peak_point = max(inside, key=lambda p: p.value)
    result.peak = round(peak_point.value, 2)
    result.peak_at = peak_point.at
    result.delta = round(peak_point.value - baseline, 2)
    result.iauc = _iauc(_slice(times, ordered, meal.eaten_at, end), baseline)

    guard_start = meal.eaten_at - timedelta(minutes=CONTAMINATION_GUARD_MIN)
    for other in other_meals or []:
        if other == meal.eaten_at:
            continue
        if guard_start <= other <= end:
            result.contaminated = True
            break
    return result


def _iauc(points: list[GlucosePoint], baseline: float) -> float | None:
    """Trapezoidal incremental AUC above baseline, mmol/L × min.

    Only the area *above* baseline counts (the standard iAUC convention):
    a dip below baseline does not cancel out an earlier rise.
    """
    if len(points) < 2:
        return None
    total = 0.0
    for left, right in zip(points, points[1:], strict=False):
        minutes = (right.at - left.at).total_seconds() / 60.0
        if minutes <= 0:
            continue
        a = max(left.value - baseline, 0.0)
        b = max(right.value - baseline, 0.0)
        total += (a + b) / 2.0 * minutes
    return round(total, 1)


def build_excursions(
    meals: list[MealLike],
    points: list[GlucosePoint],
    *,
    window_1h: tuple[int, int] = DEFAULT_WINDOW_1H,
    window_2h: tuple[int, int] = DEFAULT_WINDOW_2H,
    baseline_window: int = DEFAULT_BASELINE_WINDOW,
) -> dict[str, list[Excursion]]:
    """Excursions for every meal, keyed by window name."""
    stamps = [m.eaten_at for m in meals]
    out: dict[str, list[Excursion]] = {"1h": [], "2h": []}
    for meal in meals:
        for name, window in (("1h", window_1h), ("2h", window_2h)):
            out[name].append(
                compute_excursion(
                    meal,
                    points,
                    window=window,
                    window_name=name,
                    baseline_window=baseline_window,
                    other_meals=stamps,
                )
            )
    return out


__all__ = [
    "CONTAMINATION_GUARD_MIN",
    "DEFAULT_BASELINE_WINDOW",
    "DEFAULT_WINDOW_1H",
    "DEFAULT_WINDOW_2H",
    "Excursion",
    "GlucosePoint",
    "MealLike",
    "build_excursions",
    "compute_baseline",
    "compute_excursion",
]
