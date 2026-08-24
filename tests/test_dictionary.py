"""Personal dictionary: thresholds, prediction and deletion."""

from __future__ import annotations

import pytest

from src.db import repo
from src.vision.schemas import ItemDraft, MealDraft

pytestmark = pytest.mark.asyncio


async def _user(session):
    return await repo.get_or_create_user(session, 4242, username="tester")


async def test_a_dish_appears_only_on_the_second_sighting(session):
    user = await _user(session)
    draft = MealDraft(title="Овсянка с бананом", items=[ItemDraft(name="овсянка")])

    await repo.remember_meal(session, user, draft)
    assert await repo.list_dictionary(session, user, kind="meal") == []

    await repo.remember_meal(session, user, draft)
    entries = await repo.list_dictionary(session, user, kind="meal")
    assert [e.label for e in entries] == ["Овсянка с бананом"]
    assert entries[0].payload["items"]  # enough to rebuild the card without the model


async def test_a_medication_is_offered_after_the_first_dose(session):
    user = await _user(session)
    await repo.bump_dictionary(
        session, user, kind="medication", label="Глюкофаж 850", payload={"dose_text": "850 мг"}
    )
    entries = await repo.list_dictionary(session, user, kind="medication")
    assert [e.label for e in entries] == ["Глюкофаж 850"]


async def test_prediction_by_the_first_characters(session):
    user = await _user(session)
    for _ in range(2):
        await repo.bump_dictionary(session, user, kind="meal", label="Гречка с курицей")
    assert [e.label for e in await repo.suggest_dictionary(session, user, "гре")] == [
        "Гречка с курицей"
    ]
    # substring, not only prefix
    assert await repo.suggest_dictionary(session, user, "курицей")
    assert await repo.suggest_dictionary(session, user, "омлет") == []


async def test_suggestions_are_ordered_by_how_often_they_are_used(session):
    user = await _user(session)
    for _ in range(2):
        await repo.bump_dictionary(session, user, kind="meal", label="Кофе с молоком")
    for _ in range(5):
        await repo.bump_dictionary(session, user, kind="meal", label="Кофе чёрный")
    assert [e.label for e in await repo.suggest_dictionary(session, user, "кофе")] == [
        "Кофе чёрный",
        "Кофе с молоком",
    ]


async def test_a_deleted_entry_does_not_come_back_by_itself(session):
    user = await _user(session)
    for _ in range(2):
        entry = await repo.bump_dictionary(session, user, kind="meal", label="Сырники")
    await repo.hide_dictionary(session, entry)
    assert await repo.suggest_dictionary(session, user, "сыр") == []

    await repo.bump_dictionary(session, user, kind="meal", label="Сырники")
    assert await repo.suggest_dictionary(session, user, "сыр") == []


async def test_erasure_wipes_the_dictionary(session):
    user = await _user(session)
    for _ in range(2):
        await repo.bump_dictionary(session, user, kind="meal", label="Плов")
    await repo.delete_user_data(session, user)
    assert await repo.list_dictionary(session, user) == []
