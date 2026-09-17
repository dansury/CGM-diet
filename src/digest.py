"""Weekly digest — data access around `analytics.digest`.

The handler (`/week`) and the scheduler send the same digest, so the loading
lives here rather than in either of them (`CLAUDE.md` #6).
"""

from __future__ import annotations

from datetime import timedelta

from src.analytics.digest import WEEK_DAYS, WeeklyDigest, build_digest
from src.analytics.windows import build_excursions
from src.db import repo
from src.db.models import User
from src.handlers.deps import local_now

#: Оба окна плюс запас на базовую линию первой еды в дальнем окне.
LOOKBACK_DAYS = WEEK_DAYS * 2


async def build(session, user: User, *, days: int = WEEK_DAYS) -> WeeklyDigest:
    now = local_now(user)
    since = now - timedelta(days=days * 2)
    meals = await repo.load_meal_likes(session, user, since=since)
    points = await repo.load_points(session, user, since=since - timedelta(hours=6))
    buckets = await repo.load_activity_buckets(session, user, since=since)
    weights = [
        (row.measured_at, row.weight_kg)
        for row in await repo.load_weights(session, user, since=since)
        if row.weight_kg
    ]
    excursions = build_excursions(
        meals,
        points,
        window_1h=(user.window_1h_start, user.window_1h_end),
        window_2h=(user.window_2h_start, user.window_2h_end),
        baseline_window=user.baseline_window,
    )["1h"]
    return build_digest(
        now=now,
        meals=meals,
        excursions=excursions,
        points=points,
        buckets=buckets,
        # Последний вес каждого окна: `load_weights` отдаёт по возрастанию,
        # `build_digest` берёт первый попавший в окно, поэтому разворачиваем.
        weights=list(reversed(weights)),
        days=days,
    )


__all__ = ["LOOKBACK_DAYS", "build"]
