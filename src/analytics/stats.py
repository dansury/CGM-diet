"""Per-component postprandial statistics.

The product's central claim is “после этих блюд у вас чаще поднимается сахар”.
That claim is only worth showing when the numbers support it, so every key
(dish, component tag or packaged product) carries:

* `n` — how many excursions it was observed in;
* `mean_delta` / `median_delta` / `max_delta` — mmol/L above baseline;
* a 95% confidence interval for the mean (Student's t);
* a contrast against meals *without* that key (Mann–Whitney U, normal
  approximation with tie correction) — this is what separates “рис поднимает
  сахар” from “у меня вообще всё поднимает сахар”;
* `confidence` ∈ low|medium|high, the only thing the user sees as a verdict.

Wording rule (`spec/analytics.md` § Formulations): we report association, not
causation — «наблюдается связь», «средний подъём».

No SciPy: the whole statistics surface is small enough to implement exactly and
keep the container slim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median as _median
from statistics import stdev as _stdev

from src.analytics.windows import Excursion, MealLike

# Student's t, two-sided 95%, by degrees of freedom. df >= 30 -> normal-ish.
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
    27: 2.052, 28: 2.048, 29: 2.045,
}
_T95_LARGE = 1.96

# Thresholds that turn numbers into a verdict. Deliberately conservative:
# a "high" verdict is what licenses a concrete «уберите этот продукт».
MIN_OBSERVATIONS = 3
HIGH_N = 8
MEDIUM_N = 5
MEANINGFUL_RISE = 1.5  # mmol/L — below this a rise is not worth acting on
P_SIGNIFICANT = 0.05


@dataclass(slots=True)
class Observation:
    key: str
    delta: float
    eaten_at: object = None


@dataclass(slots=True)
class KeyStats:
    key: str
    key_type: str  # item|tag|product
    window: str
    n: int
    mean_delta: float
    median_delta: float
    max_delta: float
    sd: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    n_without: int = 0
    mean_without: float | None = None
    contrast: float | None = None  # mean_delta - mean_without
    p_value: float | None = None
    confidence: str = "low"
    label: str = ""
    examples: list[object] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        """True when the evidence justifies naming this component to the user."""
        return self.confidence in {"medium", "high"} and self.mean_delta >= MEANINGFUL_RISE


def t_critical(df: int) -> float:
    if df <= 0:
        return 0.0
    return _T95.get(df, _T95_LARGE)


def mean_ci(values: list[float]) -> tuple[float, float | None, float | None, float | None]:
    """(mean, sd, ci_low, ci_high) — a 95% t-interval for the mean."""
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return round(mean, 2), None, None, None
    sd = _stdev(values)
    half = t_critical(n - 1) * sd / math.sqrt(n)
    return round(mean, 2), round(sd, 2), round(mean - half, 2), round(mean + half, 2)


def _normal_sf(z: float) -> float:
    """P(Z > z) for the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def mann_whitney_p(a: list[float], b: list[float]) -> float | None:
    """Two-sided p for “a and b come from the same distribution”.

    Normal approximation with tie correction. Returns None when either group is
    too small for the approximation to mean anything (n < 3).
    """
    na, nb = len(a), len(b)
    if na < 3 or nb < 3:
        return None
    combined = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks: list[float] = [0.0] * len(combined)
    tie_sum = 0.0
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        size = j - i + 1
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        if size > 1:
            tie_sum += size**3 - size
        i = j + 1
    rank_a = sum(r for r, (_, group) in zip(ranks, combined, strict=True) if group == 0)
    u_a = rank_a - na * (na + 1) / 2.0
    u = min(u_a, na * nb - u_a)
    mu = na * nb / 2.0
    n = na + nb
    var = na * nb / 12.0 * ((n + 1) - tie_sum / (n * (n - 1)))
    if var <= 0:
        return None
    z = (abs(u - mu) - 0.5) / math.sqrt(var)
    return round(min(1.0, 2.0 * _normal_sf(z)), 4)


def grade_confidence(
    *, n: int, ci_low: float | None, mean_delta: float, p_value: float | None
) -> str:
    """low | medium | high — see `spec/analytics.md` § Confidence."""
    if n < MIN_OBSERVATIONS:
        return "low"
    positive = ci_low is not None and ci_low > 0
    significant = p_value is not None and p_value < P_SIGNIFICANT
    if n >= HIGH_N and positive and significant and mean_delta >= MEANINGFUL_RISE:
        return "high"
    if n >= MEDIUM_N and positive:
        return "medium"
    if n >= MEDIUM_N and significant:
        return "medium"
    return "low"


def collect_keys(meal: MealLike, key_type: str) -> list[str]:
    if key_type == "tag":
        return list(dict.fromkeys(meal.tags))
    return list(dict.fromkeys(meal.items))


def aggregate(
    meals: list[MealLike],
    excursions: list[Excursion],
    *,
    key_type: str = "tag",
    window: str = "1h",
    min_observations: int = MIN_OBSERVATIONS,
) -> list[KeyStats]:
    """Group usable excursions by key and score each key.

    `meals` and `excursions` are matched by `meal_id`; excursions without a
    baseline, without in-window readings, or contaminated by another meal are
    dropped before anything is counted.
    """
    by_id = {m.id: m for m in meals}
    usable: list[tuple[MealLike, Excursion]] = []
    for ex in excursions:
        meal = by_id.get(ex.meal_id)
        if meal is None or not ex.usable or ex.delta is None:
            continue
        usable.append((meal, ex))
    if not usable:
        return []

    per_key: dict[str, list[tuple[MealLike, Excursion]]] = {}
    for meal, ex in usable:
        for key in collect_keys(meal, key_type):
            per_key.setdefault(key, []).append((meal, ex))

    results: list[KeyStats] = []
    for key, pairs in per_key.items():
        deltas = [ex.delta for _, ex in pairs if ex.delta is not None]
        if len(deltas) < min_observations:
            continue
        without = [
            ex.delta
            for meal, ex in usable
            if key not in collect_keys(meal, key_type) and ex.delta is not None
        ]
        mean, sd, ci_low, ci_high = mean_ci(deltas)
        p_value = mann_whitney_p(deltas, without) if without else None
        mean_without = round(sum(without) / len(without), 2) if without else None
        stats = KeyStats(
            key=key,
            key_type=key_type,
            window=window,
            n=len(deltas),
            mean_delta=mean,
            median_delta=round(_median(deltas), 2),
            max_delta=round(max(deltas), 2),
            sd=sd,
            ci_low=ci_low,
            ci_high=ci_high,
            n_without=len(without),
            mean_without=mean_without,
            contrast=round(mean - mean_without, 2) if mean_without is not None else None,
            p_value=p_value,
            examples=[ex.eaten_at for _, ex in pairs][-3:],
        )
        stats.confidence = grade_confidence(
            n=stats.n, ci_low=ci_low, mean_delta=mean, p_value=p_value
        )
        results.append(stats)

    results.sort(key=lambda s: (-s.mean_delta, -s.n))
    return results


__all__ = [
    "HIGH_N",
    "MEANINGFUL_RISE",
    "MEDIUM_N",
    "MIN_OBSERVATIONS",
    "KeyStats",
    "Observation",
    "aggregate",
    "collect_keys",
    "grade_confidence",
    "mann_whitney_p",
    "mean_ci",
    "t_critical",
]
