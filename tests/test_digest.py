"""Недельный дайджест (T042): что сравнивается и о чём молчим.

Тихая ошибка здесь — сказать «стало хуже» там, где сдвинулась выборка, а не
человек. Поэтому порог, минимум наблюдений в обеих неделях и запрет на
причинные формулировки проверяются прямо.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.analytics.activity import ActivityBucket
from src.analytics.digest import (
    MEANINGFUL_SHIFT,
    WEEK_DAYS,
    build_digest,
)
from src.analytics.windows import GlucosePoint, MealLike, build_excursions
from src.reporting import format_weekly_digest

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _meal(meal_id: int, at: datetime, tags: list[str]) -> MealLike:
    return MealLike(id=meal_id, eaten_at=at, tags=tags, items=["блюдо"])


def _series(at: datetime, base: float, peak: float) -> list[GlucosePoint]:
    """Базовая линия до еды и подъём внутри окна «через час»."""
    return [
        GlucosePoint(at=at - timedelta(minutes=10), value=base),
        GlucosePoint(at=at + timedelta(minutes=60), value=peak),
    ]


def _build(meals, points, **kwargs):
    excursions = build_excursions(
        meals, points, window_1h=(45, 90), window_2h=(90, 150), baseline_window=20
    )["1h"]
    return build_digest(now=NOW, meals=meals, excursions=excursions, points=points, **kwargs)


def _week(meals_spec):
    """[(день назад, tags, подъём)] → (meals, points)."""
    meals, points = [], []
    for n, (days_ago, tags, rise) in enumerate(meals_spec, start=1):
        at = NOW - timedelta(days=days_ago, hours=1)
        meals.append(_meal(n, at, tags))
        points.extend(_series(at, 5.0, 5.0 + rise))
    return meals, points


def test_a_component_that_rose_is_named_without_a_cause():
    meals, points = _week(
        [(d, ["white_rice"], 1.0) for d in (8, 9, 10)]      # прошлая неделя
        + [(d, ["white_rice"], 3.0) for d in (1, 2, 3)]      # эта
    )
    digest = _build(meals, points)
    assert [c.key for c in digest.risen] == ["white_rice"]
    change = digest.risen[0]
    assert change.kind == "up"
    assert change.n_now == 3 and change.n_before == 3

    text = format_weekly_digest(digest)
    assert "средний подъём выше" in text
    for forbidden in ("повышает", "из-за", "вызывает", "приводит"):
        assert forbidden not in text.lower()


def test_a_shift_below_the_threshold_is_not_a_line():
    """Полделения — это дрожание выборки, а не новость."""
    small = MEANINGFUL_SHIFT / 2
    meals, points = _week(
        [(d, ["white_rice"], 2.0) for d in (8, 9, 10)]
        + [(d, ["white_rice"], 2.0 + small) for d in (1, 2, 3)]
    )
    digest = _build(meals, points)
    assert digest.risen == [] and digest.calmed == []


def test_a_component_that_calmed_down_is_reported_too():
    meals, points = _week(
        [(d, ["white_rice"], 3.5) for d in (8, 9, 10)]
        + [(d, ["white_rice"], 1.0) for d in (1, 2, 3)]
    )
    digest = _build(meals, points)
    assert [c.key for c in digest.calmed] == ["white_rice"]
    assert digest.calmed[0].kind == "down"


def test_a_new_component_is_only_news_when_it_is_visible():
    quiet, loud = _week([(d, ["salad"], 0.2) for d in (1, 2, 3)]), None
    assert _build(*quiet).risen == []

    loud = _week([(d, ["white_rice"], 3.0) for d in (1, 2, 3)])
    risen = _build(*loud).risen
    assert [c.kind for c in risen] == ["new"]


def test_the_mean_needs_enough_readings_in_both_weeks():
    """Три замера на неделе — не среднее за неделю."""
    meals, points = _week([(1, ["white_rice"], 2.0), (8, ["white_rice"], 2.0)])
    digest = _build(meals, points)
    assert digest.mean_shift is None
    assert "Средний сахар" not in format_weekly_digest(digest)


def test_the_mean_shift_is_reported_when_both_weeks_are_dense():
    meals: list[MealLike] = []
    points: list[GlucosePoint] = []
    # День 7 пропущен нарочно: он ровно на границе окон, и замер с него
    # ушёл бы в соседнюю неделю, смазав ожидаемое число.
    for day in range(0, 7):          # эта неделя, 7 замеров в день
        for hour in range(7):
            points.append(GlucosePoint(at=NOW - timedelta(days=day, hours=hour), value=8.0))
    for day in range(8, 15):         # прошлая
        for hour in range(7):
            points.append(GlucosePoint(at=NOW - timedelta(days=day, hours=hour), value=6.0))
    digest = _build(meals, points)
    assert digest.mean_shift == 2.0
    text = format_weekly_digest(digest)
    assert "Средний сахар за неделю выше" in text


def test_steps_and_weight_are_compared_when_they_exist():
    def bucket(days_ago: float, steps: int) -> ActivityBucket:
        start = NOW - timedelta(days=days_ago)
        return ActivityBucket(start_at=start, end_at=start + timedelta(hours=1), steps=steps)

    buckets = [bucket(day + 0.5, 2000) for day in range(WEEK_DAYS)]        # эта неделя
    buckets += [bucket(WEEK_DAYS + day + 0.5, 500) for day in range(WEEK_DAYS)]
    weights = [
        (NOW - timedelta(days=2), 81.0),
        (NOW - timedelta(days=9), 82.5),
    ]
    digest = _build([], [], buckets=buckets, weights=weights)
    assert (digest.steps_now, digest.steps_before) == (2000 * 7, 500 * 7)
    assert digest.steps_shift == 2000 * 7 - 500 * 7
    assert digest.weight_shift == -1.5
    text = format_weekly_digest(digest)
    assert "Шагов за неделю" in text
    assert "Вес на 1.5 кг меньше" in text


def test_nothing_to_compare_says_so_instead_of_pretending():
    digest = _build([], [])
    assert digest.has_content is False
    text = format_weekly_digest(digest)
    assert "Заметных сдвигов нет" in text


def test_the_digest_always_says_it_is_not_an_explanation():
    meals, points = _week(
        [(d, ["white_rice"], 1.0) for d in (8, 9, 10)]
        + [(d, ["white_rice"], 3.0) for d in (1, 2, 3)]
    )
    assert "не объяснение" in format_weekly_digest(_build(meals, points))


# ------------------------------------------------------------------ рассылка

async def test_the_scheduled_digest_goes_out_once_a_week(session, monkeypatch):
    """Часовой тик в понедельник не должен слать письмо каждый час."""
    from src import scheduler
    from src.db import repo
    from src.vision.schemas import GlucoseDraft, ItemDraft, MealDraft

    user = await repo.get_or_create_user(session, 909)
    user.onboarded = True
    user.tz = "UTC"
    # неделя с записями, чтобы дайджесту было о чём говорить
    for day in (1, 2, 3, 4):
        at = datetime.now(UTC) - timedelta(days=day, hours=2)
        await repo.save_meal(
            session,
            user,
            MealDraft(title="Рис", items=[ItemDraft(name="Рис белый", portion_g=150,
                                                    tags=["white_rice"])]),
            eaten_at=at,
        )
        await repo.save_glucose(
            session,
            user,
            [
                GlucoseDraft(measured_at=at - timedelta(minutes=10), value_mmol=5.0),
                GlucoseDraft(measured_at=at + timedelta(minutes=60), value_mmol=9.0),
            ],
        )
    await session.commit()

    sent: list[int] = []

    class _Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append(chat_id)

    monday = datetime(2026, 9, 14, 11, 0, tzinfo=UTC)   # понедельник, 11:00
    assert monday.weekday() == scheduler.DIGEST_WEEKDAY

    bot = _Bot()
    assert await scheduler.run_weekly_digests(bot, now=monday) == 1
    # тот же день, следующий час — слот уже занят
    assert await scheduler.run_weekly_digests(bot, now=monday + timedelta(hours=1)) == 0
    assert sent == [909]


async def test_a_switched_off_digest_is_not_sent(session):
    from src import scheduler
    from src.db import repo

    user = await repo.get_or_create_user(session, 910)
    user.onboarded = True
    user.tz = "UTC"
    user.weekly_digest_enabled = False
    await session.commit()

    class _Bot:
        async def send_message(self, chat_id, text, **kwargs):
            raise AssertionError("выключенный дайджест не должен уходить")

    monday = datetime(2026, 9, 14, 11, 0, tzinfo=UTC)
    assert await scheduler.run_weekly_digests(_Bot(), now=monday) == 0


async def test_an_empty_digest_is_not_sent_at_all(session):
    """«Сравнивать нечего» — не повод для письма."""
    from src import scheduler
    from src.db import repo

    user = await repo.get_or_create_user(session, 911)
    user.onboarded = True
    user.tz = "UTC"
    await session.commit()

    class _Bot:
        async def send_message(self, chat_id, text, **kwargs):
            raise AssertionError("пустой дайджест не должен уходить")

    monday = datetime(2026, 9, 14, 11, 0, tzinfo=UTC)
    assert await scheduler.run_weekly_digests(_Bot(), now=monday) == 0
