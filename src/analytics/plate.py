"""Harvard Healthy Eating Plate scoring for a meal session.

The plate is a proportion, not a diet: half the plate vegetables and fruit
(vegetables predominating), a quarter whole grains, a quarter protein. Potatoes
and refined grains are deliberately *not* vegetables/whole grains — they are
counted as mass but never toward a target, exactly as the original plate does.

Four things make the score honest on real data:

* people eat a lunch as several photos, so meals are grouped into *sessions*
  (a gap no larger than the user's own typical meal length, ~1 h by default);
* the daily advice is scaled by how many meals a day the user actually has —
  either the number they set, or the median number of sessions per day in
  their own history;
* a coffee or a handful of nuts is not a plate: a session is only scored once
  it holds enough actual food (`is_meal`), and a snack joins a plate only when
  a real meal lands next to it inside the session window;
* a plate whose proportions already hold together is not worth a message
  (`is_balanced`).

Nothing here touches the ORM or aiogram: it works on `PlateItem`/`PlateMeal`
(see `spec/plate.md`).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

#: категории тарелки и их доли по массе
TARGET_SHARES: dict[str, float] = {
    "veg": 0.375,
    "fruit": 0.125,
    "grain": 0.25,
    "protein": 0.25,
}
CATEGORY_LABELS: dict[str, str] = {
    "veg": "овощи",
    "fruit": "фрукты и ягоды",
    "grain": "цельные злаки",
    "protein": "белок",
    "refined": "крахмалистое и белая мука",
    "extra": "прочее",
}
#: half the plate is vegetables+fruit, and vegetables come first
HALF = ("veg", "fruit")

DEFAULT_SESSION_MIN = 60
MIN_SESSION_MIN = 30
MAX_SESSION_MIN = 120
#: за пределами этого разрыва два приёма пищи — точно разные
SESSION_SAMPLE_LIMIT_MIN = 150

DEFAULT_MEALS_PER_DAY = 3
MIN_MEALS_PER_DAY = 2
MAX_MEALS_PER_DAY = 8
#: сколько дней с едой нужно, чтобы оценивать режим по статистике
MIN_DAYS_FOR_RHYTHM = 5

#: масса одного приёма пищи, когда своей статистики ещё нет, г
DEFAULT_MEAL_MASS_G = 500.0
MIN_MEAL_MASS_G = 250.0
MAX_MEAL_MASS_G = 1200.0
#: чем считать позицию без указанной порции
FALLBACK_PORTION_G = 100.0
#: меньшие пробелы не стоят отдельной строки совета
MIN_GAP_G = 30.0

#: категории, из которых состоит собственно еда; кофе и орехи в них не входят
CORE_CATEGORIES: tuple[str, ...] = (*TARGET_SHARES, "refined")
#: столько «еды» должно быть в приёме, чтобы это была тарелка, а не перекус, г
MEAL_MIN_CORE_G = 200.0
#: с этого счёта пропорции считаем собранными — говорить не о чем
BALANCED_SCORE = 80.0

_TAG_CATEGORY: dict[str, str] = {
    "vegetable": "veg",
    "fiber": "veg",
    "legume": "protein",
    "fruit": "fruit",
    "dried_fruit": "fruit",
    "juice": "extra",
    "whole_grain": "grain",
    "white_rice": "refined",
    "refined_flour": "refined",
    "potato": "refined",
    "starch": "refined",
    "protein": "protein",
    "fish": "protein",
    "egg": "protein",
    "red_meat": "protein",
    "processed_meat": "protein",
    "cheese": "protein",
    "dairy_fermented": "protein",
    "milk": "extra",
    "nuts": "extra",
    "fat_added": "extra",
    "added_sugar": "extra",
    "sweet_drink": "extra",
    "alcohol": "extra",
    "sweetener": "extra",
    "ultra_processed": "extra",
}
#: порядок разбора: у позиции может быть несколько тегов, побеждает первый
_TAG_PRIORITY: tuple[str, ...] = (
    "vegetable",
    "fruit",
    "dried_fruit",
    "whole_grain",
    "potato",
    "white_rice",
    "refined_flour",
    "starch",
    "legume",
    "fish",
    "egg",
    "red_meat",
    "processed_meat",
    "protein",
    "cheese",
    "dairy_fermented",
    "milk",
    "nuts",
    "juice",
    "sweet_drink",
    "added_sugar",
    "alcohol",
    "fat_added",
    "sweetener",
    "ultra_processed",
    "fiber",
)


@dataclass(frozen=True, slots=True)
class PlateItem:
    """One dish component: what it is and how much of it was eaten."""

    name: str
    portion_g: float | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PlateMeal:
    id: int | None
    eaten_at: datetime
    items: list[PlateItem] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PlateScore:
    """Shares of one session against the plate."""

    mass_g: float
    grams: dict[str, float]
    shares: dict[str, float]
    score: float  # 0..100
    n_items: int
    estimated_mass: bool  # хотя бы одна порция не была названа

    @property
    def half_share(self) -> float:
        return sum(self.shares.get(cat, 0.0) for cat in HALF)


@dataclass(frozen=True, slots=True)
class MealSession:
    started_at: datetime
    ended_at: datetime
    meals: list[PlateMeal]
    items: list[PlateItem]

    @property
    def mass_g(self) -> float:
        return sum(_portion(item) for item in self.items)


@dataclass(frozen=True, slots=True)
class Gap:
    category: str
    grams: float


@dataclass(frozen=True, slots=True)
class Rhythm:
    """How the user actually eats — measured, or told to us."""

    meals_per_day: int
    meals_source: str  # user|stats|default
    session_min: int
    session_source: str  # stats|default
    meal_mass_g: float
    mass_source: str  # stats|default


@dataclass(frozen=True, slots=True)
class PlateAdvice:
    score: PlateScore
    now: list[Gap]
    day_gaps: list[Gap]
    meals_done: int
    meals_left: int
    rhythm: Rhythm


def _portion(item: PlateItem) -> float:
    value = item.portion_g
    if value is None or value <= 0:
        return FALLBACK_PORTION_G
    return float(value)


def classify(item: PlateItem) -> str:
    """Which quarter of the plate this component belongs to."""
    tags = {str(tag).strip().lower() for tag in (item.tags or [])}
    if not tags:
        from src.analytics.tags import infer_tags

        tags = set(infer_tags(item.name))
    for tag in _TAG_PRIORITY:
        if tag in tags:
            return _TAG_CATEGORY[tag]
    return "extra"


def core_mass_g(items: list[PlateItem]) -> float:
    """Масса позиций, которые вообще участвуют в тарелке, г."""
    return sum(_portion(item) for item in items if classify(item) in CORE_CATEGORIES)


def is_meal(items: list[PlateItem]) -> bool:
    """Это блюдо, а не перекус вроде кофе или горсти орехов.

    Перекус сам по себе тарелкой не оценивается; если рядом (внутри окна
    приёма пищи) человек съел что-то существенное, `group_sessions` склеит
    их в одну сессию — и перекус войдёт в состав уже настоящей тарелки.
    """
    return core_mass_g(items) >= MEAL_MIN_CORE_G


def is_balanced(score: PlateScore) -> bool:
    """Пропорции собраны — показывать разбор незачем."""
    return score.score >= BALANCED_SCORE


def score_items(items: list[PlateItem]) -> PlateScore:
    """Mass shares of one plate, and how much of the target they cover."""
    grams: dict[str, float] = defaultdict(float)
    estimated = False
    for item in items:
        if item.portion_g is None or item.portion_g <= 0:
            estimated = True
        grams[classify(item)] += _portion(item)
    total = sum(grams.values())
    shares = {cat: (value / total if total else 0.0) for cat, value in grams.items()}
    covered = sum(min(shares.get(cat, 0.0), target) for cat, target in TARGET_SHARES.items())
    score = 100.0 * covered / sum(TARGET_SHARES.values())
    return PlateScore(
        mass_g=total,
        grams=dict(grams),
        shares=shares,
        score=round(score, 1),
        n_items=len(items),
        estimated_mass=estimated,
    )


def session_window_min(meals: list[PlateMeal], *, default: int = DEFAULT_SESSION_MIN) -> int:
    """Типичная длительность приёма пищи по собственной истории, минуты.

    Берём медиану разрывов между подряд идущими записями, которые ещё могут
    быть одним приёмом (до `SESSION_SAMPLE_LIMIT_MIN`). Мало данных — час.
    """
    ordered = sorted(meals, key=lambda m: m.eaten_at)
    gaps = [
        (b.eaten_at - a.eaten_at).total_seconds() / 60.0
        for a, b in zip(ordered, ordered[1:], strict=False)
    ]
    inside = [gap for gap in gaps if 0 < gap <= SESSION_SAMPLE_LIMIT_MIN]
    if len(inside) < 3:
        return default
    value = statistics.median(inside)
    return int(max(MIN_SESSION_MIN, min(MAX_SESSION_MIN, round(value))))


def group_sessions(meals: list[PlateMeal], *, window_min: int) -> list[MealSession]:
    """Соседние записи внутри окна — один приём пищи из нескольких блюд."""
    ordered = sorted(meals, key=lambda m: m.eaten_at)
    sessions: list[MealSession] = []
    bucket: list[PlateMeal] = []
    for meal in ordered:
        if bucket and meal.eaten_at - bucket[-1].eaten_at > timedelta(minutes=window_min):
            sessions.append(_session(bucket))
            bucket = []
        bucket.append(meal)
    if bucket:
        sessions.append(_session(bucket))
    return sessions


def _session(bucket: list[PlateMeal]) -> MealSession:
    items = [item for meal in bucket for item in meal.items]
    return MealSession(
        started_at=bucket[0].eaten_at,
        ended_at=bucket[-1].eaten_at,
        meals=list(bucket),
        items=items,
    )


def estimate_meals_per_day(
    meals: list[PlateMeal], *, window_min: int, tzinfo=None
) -> int | None:
    """Сколько приёмов пищи в день у пользователя — медиана по дням с едой.

    `None`, если дней с записями меньше `MIN_DAYS_FOR_RHYTHM`: режим по двум
    дням — это не статистика.
    """
    sessions = group_sessions(meals, window_min=window_min)
    if not sessions:
        return None
    per_day: Counter[object] = Counter()
    for session in sessions:
        stamp = session.started_at
        if tzinfo is not None:
            stamp = stamp.astimezone(tzinfo)
        per_day[stamp.date()] += 1
    if len(per_day) < MIN_DAYS_FOR_RHYTHM:
        return None
    value = statistics.median(sorted(per_day.values()))
    return int(max(MIN_MEALS_PER_DAY, min(MAX_MEALS_PER_DAY, round(value))))


def typical_meal_mass(meals: list[PlateMeal], *, window_min: int) -> float | None:
    """Медианная масса одного приёма пищи, г. `None` — данных мало."""
    sessions = group_sessions(meals, window_min=window_min)
    masses = [session.mass_g for session in sessions if session.mass_g > 0]
    if len(masses) < MIN_DAYS_FOR_RHYTHM:
        return None
    value = statistics.median(masses)
    return float(max(MIN_MEAL_MASS_G, min(MAX_MEAL_MASS_G, value)))


def measure_rhythm(
    history: list[PlateMeal],
    *,
    meals_per_day: int | None = None,
    tzinfo=None,
) -> Rhythm:
    """Собрать режим питания: настройка пользователя важнее статистики."""
    window = session_window_min(history)
    session_source = "stats" if window != DEFAULT_SESSION_MIN else "default"
    measured = estimate_meals_per_day(history, window_min=window, tzinfo=tzinfo)
    if meals_per_day:
        count, meals_source = int(meals_per_day), "user"
    elif measured:
        count, meals_source = measured, "stats"
    else:
        count, meals_source = DEFAULT_MEALS_PER_DAY, "default"
    mass = typical_meal_mass(history, window_min=window)
    return Rhythm(
        meals_per_day=count,
        meals_source=meals_source,
        session_min=window,
        session_source=session_source,
        meal_mass_g=mass if mass else DEFAULT_MEAL_MASS_G,
        mass_source="stats" if mass else "default",
    )


def _gaps(grams: dict[str, float], targets: dict[str, float]) -> list[Gap]:
    out = [
        Gap(category=cat, grams=round(target - grams.get(cat, 0.0)))
        for cat, target in targets.items()
        if target - grams.get(cat, 0.0) >= MIN_GAP_G
    ]
    return sorted(out, key=lambda gap: gap.grams, reverse=True)


def advise(
    *,
    current: MealSession,
    day_sessions: list[MealSession],
    rhythm: Rhythm,
) -> PlateAdvice:
    """Чего добрать сейчас и что остаётся на оставшиеся приёмы пищи.

    `day_sessions` — все приёмы за сегодня, включая текущий: дневной остаток
    считается от того, что уже съедено, а не от одной последней тарелки.
    Перекусы идут в дневную массу, но приёмом пищи не считаются.
    """
    score = score_items(current.items)
    meal_targets = {cat: rhythm.meal_mass_g * share for cat, share in TARGET_SHARES.items()}
    now = _gaps(score.grams, meal_targets)

    day_grams: dict[str, float] = defaultdict(float)
    for session in day_sessions:
        for cat, value in score_items(session.items).grams.items():
            day_grams[cat] += value
    meals_done = sum(1 for session in day_sessions if is_meal(session.items))
    meals_left = max(0, rhythm.meals_per_day - meals_done)
    day_targets = {cat: value * rhythm.meals_per_day for cat, value in meal_targets.items()}
    return PlateAdvice(
        score=score,
        now=now,
        day_gaps=_gaps(dict(day_grams), day_targets),
        meals_done=meals_done,
        meals_left=meals_left,
        rhythm=rhythm,
    )


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


__all__ = [
    "BALANCED_SCORE",
    "CATEGORY_LABELS",
    "CORE_CATEGORIES",
    "DEFAULT_MEALS_PER_DAY",
    "DEFAULT_SESSION_MIN",
    "MAX_MEALS_PER_DAY",
    "MEAL_MIN_CORE_G",
    "MIN_MEALS_PER_DAY",
    "TARGET_SHARES",
    "Gap",
    "MealSession",
    "PlateAdvice",
    "PlateItem",
    "PlateMeal",
    "PlateScore",
    "Rhythm",
    "advise",
    "category_label",
    "classify",
    "core_mass_g",
    "estimate_meals_per_day",
    "group_sessions",
    "is_balanced",
    "is_meal",
    "measure_rhythm",
    "score_items",
    "session_window_min",
    "typical_meal_mass",
]
