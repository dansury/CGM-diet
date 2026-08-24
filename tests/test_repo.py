"""Persistence layer: writes, idempotency, glossary, erasure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.db import repo
from src.vision.schemas import (
    GlucoseDraft,
    ItemDraft,
    LabDraft,
    MarkerDraft,
    MealDraft,
    ProductDraft,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


async def test_get_or_create_is_idempotent_and_seeds_the_glossary(session):
    first = await repo.get_or_create_user(session, 42, first_name="A")
    second = await repo.get_or_create_user(session, 42)
    assert first.id == second.id
    symptoms = await repo.list_symptoms(session, first, limit=100)
    assert len(symptoms) == len(repo.SEED_SYMPTOMS)


async def test_save_meal_stores_items_totals_and_normalised_names(session):
    user = await repo.get_or_create_user(session, 1)
    draft = MealDraft(
        title="Обед",
        items=[
            ItemDraft(name="Рис белый, 150 г", portion_g=150, kcal=200, carbs_g=45, tags=["white_rice"]),
            ItemDraft(name="Курица", portion_g=100, kcal=165, protein_g=31, tags=["protein"]),
        ],
    )
    meal = await repo.save_meal(session, user, draft, eaten_at=NOW)
    assert meal.kcal == pytest.approx(365.0)
    assert meal.carbs_g == pytest.approx(45.0)
    assert {i.name_norm for i in meal.items} == {"рис белый", "курица"}


async def test_save_glucose_skips_exact_duplicates(session):
    user = await repo.get_or_create_user(session, 2)
    drafts = [GlucoseDraft(measured_at=NOW, value_mmol=7.4)]
    assert len(await repo.save_glucose(session, user, drafts)) == 1
    assert await repo.save_glucose(session, user, drafts) == []


async def test_load_points_returns_timezone_aware_values(session):
    user = await repo.get_or_create_user(session, 3)
    await repo.save_glucose(session, user, [GlucoseDraft(measured_at=NOW, value_mmol=6.0)])
    points = await repo.load_points(session, user)
    assert points[0].at.tzinfo is not None


async def test_product_upsert_merges_a_second_scan(session):
    user = await repo.get_or_create_user(session, 4)
    first = await repo.save_product(
        session, user, ProductDraft(name="Йогурт", barcode="4600000000017", kcal_100=86)
    )
    second = await repo.save_product(
        session,
        user,
        ProductDraft(name="Йогурт", barcode="4600000000017", sugars_100=11.9, flags=["added_sugar"]),
    )
    assert first.id == second.id
    assert second.kcal_100 == 86  # kept from the first side
    assert second.sugars_100 == 11.9  # added by the second
    assert second.flags == ["added_sugar"]


async def test_upsert_symptom_grows_the_personal_glossary(session):
    user = await repo.get_or_create_user(session, 5)
    created = await repo.upsert_symptom(session, user, "звон в ушах")
    again = await repo.upsert_symptom(session, user, "звон в ушах")
    assert created.id == again.id
    labels = [s.label for s in await repo.list_symptoms(session, user, limit=100)]
    assert "звон в ушах" in labels


async def test_checkin_counts_hits_and_reorders_the_buttons(session):
    user = await repo.get_or_create_user(session, 6)
    for _ in range(3):
        await repo.save_checkin(
            session, user, at=NOW, score=2, symptom_labels=["потливость"]
        )
    top = (await repo.list_symptoms(session, user))[0]
    assert top.label == "потливость"
    assert top.hits == 3


async def test_labs_are_flagged_against_the_reference_range(session):
    user = await repo.get_or_create_user(session, 7)
    draft = LabDraft(
        panel="Биохимия",
        taken_at=NOW,
        markers=[MarkerDraft(marker="Глюкоза", value=6.3, ref_low=3.9, ref_high=5.9)],
    )
    rows = await repo.save_labs(session, user, draft)
    assert rows[0].flag == "high"


async def test_activity_ingest_is_idempotent(session):
    from src.db.models import ActivitySample

    user = await repo.get_or_create_user(session, 8)
    samples = [
        ActivitySample(
            external_id="a1", kind="steps", start_at=NOW, end_at=NOW + timedelta(minutes=15), steps=500
        )
    ]
    assert await repo.upsert_activity(session, user, samples) == 1
    again = [
        ActivitySample(
            external_id="a1", kind="steps", start_at=NOW, end_at=NOW + timedelta(minutes=15), steps=500
        )
    ]
    assert await repo.upsert_activity(session, user, again) == 0


async def test_delete_user_data_wipes_everything_but_keeps_the_account(session):
    user = await repo.get_or_create_user(session, 9)
    await repo.save_meal(
        session, user, MealDraft(title="x", items=[ItemDraft(name="хлеб")]), eaten_at=NOW
    )
    await repo.save_glucose(session, user, [GlucoseDraft(measured_at=NOW, value_mmol=7.0)])
    await repo.save_checkin(session, user, at=NOW, score=3, symptom_labels=["слабость"])
    await repo.delete_user_data(session, user)
    totals = await repo.counts(session, user)
    assert set(totals.values()) == {0}
    assert await repo.get_user(session, 9) is not None


async def test_tapping_a_seeded_symptom_does_not_create_a_duplicate(session):
    """Seeded rows have an English slug and a Russian label — match on the label."""
    user = await repo.get_or_create_user(session, 10)
    before = len(await repo.list_symptoms(session, user, limit=100))
    seeded = next(s for s in await repo.list_symptoms(session, user, limit=100)
                  if s.label == "сонливость")
    await repo.save_checkin(session, user, at=NOW, score=2, symptom_labels=["Сонливость"])
    after = await repo.list_symptoms(session, user, limit=100)
    assert len(after) == before
    await session.refresh(seeded)
    assert seeded.hits == 1
