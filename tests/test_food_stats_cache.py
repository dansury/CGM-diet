"""The `/stats` cache: what it answers, and what drops it.

The silent failure here is a stale answer — new sugar readings that never reach
the numbers — so the tests watch invalidation, not speed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src import food_stats
from src.db import repo
from src.db.models import FoodStat
from src.vision.schemas import GlucoseDraft, ItemDraft, MealDraft

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


async def _seed(session, *, meals: int = 4, rise: float = 3.0):
    """A user with `meals` clean rice excursions and nothing else going on."""
    user = await repo.get_or_create_user(session, 77)
    for day in range(meals):
        eaten = NOW - timedelta(days=day + 1)
        draft = MealDraft(
            title="Рис",
            items=[ItemDraft(name="Рис белый", portion_g=150, carbs_g=45, tags=["white_rice"])],
        )
        await repo.save_meal(session, user, draft, eaten_at=eaten)
        await repo.save_glucose(
            session,
            user,
            [
                GlucoseDraft(measured_at=eaten - timedelta(minutes=10), value_mmol=5.0),
                GlucoseDraft(measured_at=eaten + timedelta(minutes=60), value_mmol=5.0 + rise),
                # the 2h window (90–150 min) needs its own reading, otherwise
                # only half the cache is filled
                GlucoseDraft(measured_at=eaten + timedelta(minutes=120), value_mmol=5.0 + rise / 2),
            ],
        )
    await session.flush()
    return user


async def test_second_call_is_served_from_the_table(session):
    user = await _seed(session)
    first = await food_stats.stats_for(session, user, key_type="tag", window="1h")
    assert [s.key for s in first] == ["white_rice"]
    assert user.food_stats_at is not None

    rows = list(await session.scalars(select(FoodStat).where(FoodStat.user_id == user.id)))
    # one rebuild fills every (key_type, window) pair, not just the one asked for
    assert {(r.key_type, r.window) for r in rows} == {
        (k, w) for k in food_stats.KEY_TYPES for w in food_stats.WINDOWS
    }

    cached = await repo.load_food_stats(
        session, user, key_type="tag", window="1h", ttl=food_stats.CACHE_TTL
    )
    assert cached is not None
    assert [(s.key, s.n, s.mean_delta) for s in cached] == [
        (s.key, s.n, s.mean_delta) for s in first
    ]
    assert cached[0].contrast == first[0].contrast
    assert cached[0].confidence == first[0].confidence


async def test_a_new_reading_drops_the_cache(session):
    user = await _seed(session)
    await food_stats.stats_for(session, user)
    assert user.food_stats_at is not None

    await repo.save_glucose(
        session, user, [GlucoseDraft(measured_at=NOW, value_mmol=6.1)]
    )
    assert user.food_stats_at is None
    assert (
        await repo.load_food_stats(
            session, user, key_type="tag", window="1h", ttl=food_stats.CACHE_TTL
        )
        is None
    )


async def test_a_new_meal_drops_the_cache(session):
    user = await _seed(session)
    await food_stats.stats_for(session, user)
    await repo.save_meal(
        session,
        user,
        MealDraft(title="Гречка", items=[ItemDraft(name="Гречка", portion_g=100)]),
        eaten_at=NOW,
    )
    assert user.food_stats_at is None


async def test_a_duplicate_reading_keeps_the_cache(session):
    """Nothing was inserted — the numbers cannot have moved."""
    user = await _seed(session)
    await food_stats.stats_for(session, user)
    stamp = user.food_stats_at
    eaten = NOW - timedelta(days=1)
    saved = await repo.save_glucose(
        session, user, [GlucoseDraft(measured_at=eaten - timedelta(minutes=10), value_mmol=5.0)]
    )
    assert saved == []
    assert user.food_stats_at == stamp


async def test_the_period_slides_so_the_cache_expires(session):
    user = await _seed(session)
    await food_stats.stats_for(session, user)
    user.food_stats_at = datetime.now(UTC) - food_stats.CACHE_TTL - timedelta(minutes=1)
    assert (
        await repo.load_food_stats(
            session, user, key_type="tag", window="1h", ttl=food_stats.CACHE_TTL
        )
        is None
    )


async def test_nothing_to_show_is_cached_too(session):
    """A user below `min_observations` must not rebuild on every tap."""
    user = await _seed(session, meals=1)
    assert await food_stats.stats_for(session, user) == []
    assert user.food_stats_at is not None
    assert (
        await repo.load_food_stats(
            session, user, key_type="tag", window="1h", ttl=food_stats.CACHE_TTL
        )
        == []
    )


async def test_erasure_takes_the_cache_with_it(session):
    user = await _seed(session)
    await food_stats.stats_for(session, user)
    await repo.delete_user_data(session, user)
    rows = list(await session.scalars(select(FoodStat).where(FoodStat.user_id == user.id)))
    assert rows == []
    assert user.food_stats_at is None
