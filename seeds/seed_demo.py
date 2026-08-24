"""Deterministic demo dataset: 14 days of meals, CGM, check-ins and steps.

Run it against a local DB to see `/stats`, `/graph` and the recommendations with
real-looking numbers:

    DATABASE_URL=sqlite+aiosqlite:///data/cgm.db python -m seeds.seed_demo 111222333

The generator is *deliberately biased*: meals tagged `added_sugar` /
`refined_flour` get a large postprandial rise, `vegetable` / `protein` meals a
small one, and a walk after a meal shaves part of the rise off. That makes the
statistics layer's verdicts checkable by eye — and it is the same fixture the
end-to-end test uses.
"""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repo
from src.db.models import ActivitySample
from src.vision.schemas import GlucoseDraft, ItemDraft, MealDraft

SEED = 20260824
DAYS = 14

# (title, items with tags, rise in mmol/L that this meal tends to produce)
MENU: tuple[tuple[str, list[tuple[str, list[str], float]], float], ...] = (
    ("Овсянка с бананом и мёдом", [("овсяная каша", ["whole_grain"], 200.0),
                                   ("банан", ["fruit", "high_gi"], 120.0),
                                   ("мёд", ["added_sugar"], 20.0)], 3.6),
    ("Белый хлеб с джемом", [("белый хлеб", ["refined_flour"], 70.0),
                             ("джем", ["added_sugar"], 30.0)], 4.1),
    ("Гречка с курицей", [("гречневая каша", ["whole_grain"], 180.0),
                          ("куриная грудка", ["protein"], 120.0)], 1.4),
    ("Омлет с овощами", [("яйца", ["egg", "protein"], 120.0),
                         ("овощи", ["vegetable", "fiber"], 150.0)], 0.7),
    ("Салат с лососем", [("лосось", ["fish", "protein"], 130.0),
                         ("салат", ["vegetable", "fiber"], 160.0)], 0.6),
    ("Паста с соусом", [("макароны", ["refined_flour"], 220.0),
                        ("томатный соус", ["vegetable"], 80.0)], 3.2),
    ("Творог с ягодами", [("творог", ["dairy_fermented", "protein"], 180.0),
                          ("ягоды", ["fruit", "fiber"], 80.0)], 1.1),
)

SYMPTOM_POOL = ("сонливость", "потливость", "туман в голове", "сильный голод")


async def build_demo(session: AsyncSession, tg_id: int, *, days: int = DAYS) -> dict[str, int]:
    """Populate one user with a full history. Returns row counts."""
    rng = random.Random(SEED)
    user = await repo.get_or_create_user(session, tg_id, first_name="Demo")
    start = datetime.now(UTC).replace(
        hour=6, minute=0, second=0, microsecond=0
    ) - timedelta(days=days)

    activity: list[ActivitySample] = []
    for day in range(days):
        day_start = start + timedelta(days=day)
        # three meals a day at roughly 8:00 / 13:30 / 19:00
        for slot, hour in enumerate((8, 13, 19)):
            title, items, rise = MENU[rng.randrange(len(MENU))]
            eaten_at = day_start.replace(hour=hour) + timedelta(minutes=rng.randint(-25, 25))
            draft = MealDraft(
                title=title,
                source="photo",
                confidence=0.7,
                items=[
                    ItemDraft(
                        name=name,
                        portion_g=portion,
                        kcal=round(portion * rng.uniform(0.8, 2.0), 1),
                        protein_g=round(portion * 0.08, 1),
                        fat_g=round(portion * 0.05, 1),
                        carbs_g=round(portion * 0.2, 1),
                        fiber_g=round(portion * 0.02, 1),
                        tags=tags,
                    )
                    for name, tags, portion in items
                ],
            )
            await repo.save_meal(session, user, draft, eaten_at=eaten_at)

            walked = slot == 1 and day % 2 == 0
            steps = rng.randint(1800, 3200) if walked else rng.randint(30, 300)
            activity.append(
                ActivitySample(
                    external_id=f"demo-{day}-{slot}",
                    kind="steps",
                    start_at=eaten_at,
                    end_at=eaten_at + timedelta(minutes=60),
                    steps=steps,
                    source="demo",
                )
            )

            baseline = rng.uniform(5.0, 5.8)
            effective_rise = rise * (0.55 if walked else 1.0) * rng.uniform(0.85, 1.15)
            curve = [
                (-15, baseline + rng.uniform(-0.15, 0.15)),
                (20, baseline + effective_rise * 0.55),
                (45, baseline + effective_rise * 0.9),
                (65, baseline + effective_rise),
                (95, baseline + effective_rise * 0.55),
                (130, baseline + effective_rise * 0.2),
            ]
            await repo.save_glucose(
                session,
                user,
                [
                    GlucoseDraft(
                        measured_at=eaten_at + timedelta(minutes=offset),
                        value_mmol=round(value, 2),
                    )
                    for offset, value in curve
                ],
                source="cgm_api",
            )

            # A big rise makes a bad check-in more likely — that is the signal
            # the symptom statistics are supposed to find.
            if rng.random() < (0.65 if effective_rise > 2.5 else 0.2):
                await repo.save_checkin(
                    session,
                    user,
                    at=eaten_at + timedelta(minutes=75),
                    score=2 if effective_rise > 2.5 else 4,
                    symptom_labels=(
                        [SYMPTOM_POOL[rng.randrange(2)]] if effective_rise > 2.5 else []
                    ),
                    source="buttons",
                )

    await repo.upsert_activity(session, user, activity)
    return await repo.counts(session, user)


async def _main(tg_id: int) -> None:
    from src.db.engine import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as session:
        totals = await build_demo(session, tg_id)
        await session.commit()
    print("seeded:", totals)


if __name__ == "__main__":
    asyncio.run(_main(int(sys.argv[1]) if len(sys.argv) > 1 else 111222333))
