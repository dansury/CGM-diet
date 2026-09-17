"""Weekly digest: what moved in a person's own numbers since last week.

`/stats` answers "what is true over thirty days" — a slow, stable picture that
looks the same on Monday and on Friday. The question a person actually asks
themselves on Monday is a different one: *did anything change?* This module
answers only that, by putting two equal weeks side by side.

What it deliberately does not do:

* invent a cause. A component that rose this week rose *this week*; the digest
  says so and stops (`spec/clinical.md` — association, never causation);
* report a change that rests on nothing. Both weeks must carry enough
  observations, or the line is not produced at all — `MIN_WEEK_POINTS` for the
  glucose series, `stats.MIN_OBSERVATIONS` for a component;
* rank people against a norm. Everything here is a person against themselves.

Pure over the analytics types: no ORM, no aiogram (`CLAUDE.md` #7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.analytics.activity import ActivityBucket
from src.analytics.cgm_metrics import CGMSummary, summarize
from src.analytics.stats import KeyStats, aggregate
from src.analytics.windows import Excursion, GlucosePoint, MealLike

#: Дней в окне. Неделя — самый короткий срок, на котором видно режим,
#: и самый длинный, который человек помнит целиком.
WEEK_DAYS = 7

#: Меньше замеров за неделю — сравнивать нечего: разница будет шумом.
MIN_WEEK_POINTS = 20

#: На сколько ммоль/л должен сдвинуться средний подъём, чтобы это назвать
#: изменением, а не дрожанием выборки.
MEANINGFUL_SHIFT = 0.8

#: Столько же — для среднего сахара за неделю.
MEANINGFUL_MEAN_SHIFT = 0.5

#: Процентные пункты TIR, ниже которых движение не стоит слов.
MEANINGFUL_TIR_SHIFT = 5.0


@dataclass(slots=True)
class KeyChange:
    """Один компонент: что с ним было неделю назад и что стало."""

    key: str
    key_type: str
    now: float | None                # средний подъём на этой неделе
    before: float | None             # на прошлой
    n_now: int = 0
    n_before: int = 0
    kind: str = "same"               # new|gone|up|down|same

    @property
    def delta(self) -> float | None:
        if self.now is None or self.before is None:
            return None
        return round(self.now - self.before, 2)


@dataclass(slots=True)
class WeeklyDigest:
    """Две равные недели рядом. Пустые поля — значит, сравнивать было нечего."""

    since: datetime
    until: datetime
    meals_now: int = 0
    meals_before: int = 0
    days_with_meals: int = 0
    glucose_now: CGMSummary | None = None
    glucose_before: CGMSummary | None = None
    risen: list[KeyChange] = field(default_factory=list)
    calmed: list[KeyChange] = field(default_factory=list)
    steps_now: int | None = None
    steps_before: int | None = None
    weight_now: float | None = None
    weight_before: float | None = None

    @property
    def mean_shift(self) -> float | None:
        if not self.glucose_now or not self.glucose_before:
            return None
        if self.glucose_now.n < MIN_WEEK_POINTS or self.glucose_before.n < MIN_WEEK_POINTS:
            return None
        return round(self.glucose_now.mean - self.glucose_before.mean, 2)

    @property
    def tir_shift(self) -> float | None:
        if not self.glucose_now or not self.glucose_before:
            return None
        if self.glucose_now.tir is None or self.glucose_before.tir is None:
            return None
        if self.glucose_now.n < MIN_WEEK_POINTS or self.glucose_before.n < MIN_WEEK_POINTS:
            return None
        return round(self.glucose_now.tir - self.glucose_before.tir, 1)

    @property
    def steps_shift(self) -> int | None:
        if self.steps_now is None or self.steps_before is None:
            return None
        return self.steps_now - self.steps_before

    @property
    def weight_shift(self) -> float | None:
        if self.weight_now is None or self.weight_before is None:
            return None
        return round(self.weight_now - self.weight_before, 1)

    @property
    def has_content(self) -> bool:
        """Есть ли что сказать. Пустой дайджест не отправляется."""
        return bool(
            self.meals_now
            or self.risen
            or self.calmed
            or self.mean_shift is not None
            or self.steps_shift is not None
            or self.weight_shift is not None
        )


def build_digest(
    *,
    now: datetime,
    meals: list[MealLike],
    excursions: list[Excursion],
    points: list[GlucosePoint],
    buckets: list[ActivityBucket] | None = None,
    weights: list[tuple[datetime, float]] | None = None,
    key_type: str = "tag",
    days: int = WEEK_DAYS,
) -> WeeklyDigest:
    """Две недели подряд из одного набора данных за 14 дней.

    `meals`/`excursions`/`points` должны покрывать оба окна: делить их здесь
    дешевле, чем дважды ходить в базу за пересекающимися периодами.
    """
    until = now
    since = now - timedelta(days=days)
    before_since = since - timedelta(days=days)

    meals_now = [m for m in meals if since <= m.eaten_at <= until]
    meals_before = [m for m in meals if before_since <= m.eaten_at < since]
    digest = WeeklyDigest(
        since=since,
        until=until,
        meals_now=len(meals_now),
        meals_before=len(meals_before),
        days_with_meals=len({m.eaten_at.date() for m in meals_now}),
    )

    points_now = [p for p in points if since <= p.at <= until]
    points_before = [p for p in points if before_since <= p.at < since]
    if points_now:
        digest.glucose_now = summarize(points_now)
    if points_before:
        digest.glucose_before = summarize(points_before)

    digest.risen, digest.calmed = _key_changes(
        meals_now, meals_before, excursions, since, before_since, until, key_type
    )

    if buckets is not None:
        now_steps = _steps_in(buckets, since, until)
        before_steps = _steps_in(buckets, before_since, since)
        if now_steps or before_steps:
            digest.steps_now, digest.steps_before = now_steps, before_steps

    for stamp, value in weights or []:
        if since <= stamp <= until:
            digest.weight_now = value if digest.weight_now is None else digest.weight_now
        elif before_since <= stamp < since:
            digest.weight_before = value
    return digest


def _steps_in(buckets: list[ActivityBucket], since: datetime, until: datetime) -> int:
    return sum(b.steps or 0 for b in buckets if since <= b.start_at < until)


def _key_changes(
    meals_now: list[MealLike],
    meals_before: list[MealLike],
    excursions: list[Excursion],
    since: datetime,
    before_since: datetime,
    until: datetime,
    key_type: str,
) -> tuple[list[KeyChange], list[KeyChange]]:
    """Компоненты, чей средний подъём сдвинулся заметно, в обе стороны."""
    ids_now = {m.id for m in meals_now}
    ids_before = {m.id for m in meals_before}
    stats_now = {
        s.key: s
        for s in aggregate(
            meals_now, [e for e in excursions if e.meal_id in ids_now], key_type=key_type
        )
    }
    stats_before = {
        s.key: s
        for s in aggregate(
            meals_before, [e for e in excursions if e.meal_id in ids_before], key_type=key_type
        )
    }

    risen: list[KeyChange] = []
    calmed: list[KeyChange] = []
    for key in set(stats_now) | set(stats_before):
        change = _change_for(key, key_type, stats_now.get(key), stats_before.get(key))
        if change is None:
            continue
        (risen if change.kind in {"new", "up"} else calmed).append(change)

    risen.sort(key=lambda c: -(c.now or 0))
    calmed.sort(key=lambda c: -(c.before or 0))
    return risen, calmed


def _change_for(
    key: str, key_type: str, now: KeyStats | None, before: KeyStats | None
) -> KeyChange | None:
    """None — когда движение не выходит за порог или его не на чем построить."""
    if now is not None and before is not None:
        shift = now.mean_delta - before.mean_delta
        if abs(shift) < MEANINGFUL_SHIFT:
            return None
        return KeyChange(
            key=key,
            key_type=key_type,
            now=now.mean_delta,
            before=before.mean_delta,
            n_now=now.n,
            n_before=before.n,
            kind="up" if shift > 0 else "down",
        )
    if now is not None:
        # Новое на этой неделе показываем, только если оно само по себе заметно:
        # «появился салат, +0.3» — не новость.
        if now.mean_delta < MEANINGFUL_SHIFT:
            return None
        return KeyChange(key=key, key_type=key_type, now=now.mean_delta,
                         before=None, n_now=now.n, kind="new")
    if before is not None and before.mean_delta >= MEANINGFUL_SHIFT:
        return KeyChange(key=key, key_type=key_type, now=None,
                         before=before.mean_delta, n_before=before.n, kind="gone")
    return None


__all__ = [
    "MEANINGFUL_MEAN_SHIFT",
    "MEANINGFUL_SHIFT",
    "MEANINGFUL_TIR_SHIFT",
    "MIN_WEEK_POINTS",
    "WEEK_DAYS",
    "KeyChange",
    "WeeklyDigest",
    "build_digest",
]
