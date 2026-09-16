"""Cached per-key postprandial statistics — the numbers behind `/stats`.

Every tap of a window button used to reload thirty days of meals and readings,
rebuild every excursion and re-run the aggregate; four buttons, two key types
and a product verdict multiply that by six over one conversation. The answer
only changes when a meal or a reading changes, so it is cached in `food_stats`
(`spec/data_model.md`) and dropped the moment new data lands.

One excursion build feeds all four (key_type, window) pairs, so a miss fills
the whole cache rather than the bucket that was asked for.
"""

from __future__ import annotations

from datetime import timedelta

from src.analytics.stats import KeyStats, aggregate
from src.analytics.windows import build_excursions
from src.config import load_settings
from src.db import repo
from src.db.models import User
from src.handlers.deps import local_now

#: Cached buckets, filled together on every rebuild.
KEY_TYPES: tuple[str, ...] = ("tag", "item")
WINDOWS: tuple[str, ...] = ("1h", "2h")

DEFAULT_PERIOD_DAYS = 30

#: The 30-day period slides with the clock, so even untouched data goes stale.
#: An hour is short enough that nothing a user would notice ages out unseen,
#: and long enough to cover one conversation with the bot.
CACHE_TTL = timedelta(hours=1)


async def stats_for(
    session,
    user: User,
    *,
    key_type: str = "tag",
    window: str = "1h",
    days: int = DEFAULT_PERIOD_DAYS,
) -> list[KeyStats]:
    """Cached aggregate for one bucket; rebuilds the whole cache on a miss.

    A non-default period is computed and not cached: the cache holds exactly
    one period, and mixing two would make the stamp meaningless.
    """
    if days != DEFAULT_PERIOD_DAYS:
        return (await _compute(session, user, days=days)).get((key_type, window), [])
    cached = await repo.load_food_stats(
        session, user, key_type=key_type, window=window, ttl=CACHE_TTL
    )
    if cached is not None:
        return cached
    buckets = await _compute(session, user, days=days)
    await repo.save_food_stats(session, user, buckets)
    return buckets.get((key_type, window), [])


async def rebuild(session, user: User, *, days: int = DEFAULT_PERIOD_DAYS) -> None:
    """Recompute and store the cache unconditionally."""
    await repo.save_food_stats(session, user, await _compute(session, user, days=days))


async def _compute(
    session, user: User, *, days: int
) -> dict[tuple[str, str], list[KeyStats]]:
    since = local_now(user) - timedelta(days=days)
    meals = await repo.load_meal_likes(session, user, since=since)
    points = await repo.load_points(session, user, since=since - timedelta(hours=6))
    if not meals or not points:
        return {}
    excursions = build_excursions(
        meals,
        points,
        window_1h=(user.window_1h_start, user.window_1h_end),
        window_2h=(user.window_2h_start, user.window_2h_end),
        baseline_window=user.baseline_window,
    )
    min_observations = load_settings().min_observations
    out: dict[tuple[str, str], list[KeyStats]] = {}
    for window in WINDOWS:
        for key_type in KEY_TYPES:
            out[(key_type, window)] = aggregate(
                meals,
                excursions[window],
                key_type=key_type,
                window=window,
                min_observations=min_observations,
            )
    return out


__all__ = ["CACHE_TTL", "KEY_TYPES", "WINDOWS", "rebuild", "stats_for"]
