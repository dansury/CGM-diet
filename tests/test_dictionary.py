"""Personal dictionary: thresholds, prediction and deletion."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db import repo
from src.vision.schemas import ItemDraft, MealDraft, ProductDraft

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


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


async def test_every_item_can_be_pinned_with_one_button(session):
    """Ждать второго раза необязательно: кнопка кладёт позицию в словарь сразу."""
    user = await _user(session)
    draft = MealDraft(
        title="Гречка с курицей",
        items=[ItemDraft(name="гречка"), ItemDraft(name="курица")],
    )
    await repo.remember_meal(session, user, draft)

    offered = await repo.pinnable_entries(session, user, draft)
    # по кнопке на каждую позицию и одна на само блюдо
    assert [e.label for e in offered] == ["гречка", "курица", "Гречка с курицей"]
    assert await repo.list_dictionary(session, user, kind="item") == []

    await repo.pin_dictionary(session, offered[0])
    assert [e.label for e in await repo.list_dictionary(session, user, kind="item")] == ["гречка"]
    # остальные позиции всё ещё ждут своей кнопки
    assert [e.label for e in await repo.pinnable_entries(session, user, draft)] == [
        "курица",
        "Гречка с курицей",
    ]


async def test_a_single_item_meal_offers_one_button_not_two(session):
    user = await _user(session)
    draft = MealDraft(title="Овсянка", items=[ItemDraft(name="овсянка")])
    await repo.remember_meal(session, user, draft)
    assert [e.label for e in await repo.pinnable_entries(session, user, draft)] == ["овсянка"]


async def test_a_deleted_shortcut_is_not_offered_again(session):
    user = await _user(session)
    draft = MealDraft(title="Кефир", items=[ItemDraft(name="кефир")])
    await repo.remember_meal(session, user, draft)
    entry = (await repo.pinnable_entries(session, user, draft))[0]
    await repo.hide_dictionary(session, entry)
    assert await repo.pinnable_entries(session, user, draft) == []


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


async def test_a_pinned_entry_outranks_the_rotation(session):
    user = await _user(session)
    for _ in range(2):
        pinned = await repo.bump_dictionary(session, user, kind="meal", label="Кофе чёрный")
    pinned.pinned = True
    for _ in range(2):
        await repo.bump_dictionary(session, user, kind="meal", label="Кофе с молоком")
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


async def test_every_entity_lands_in_the_dictionary(session):
    """Еда, её составляющие, упаковка, лекарство и самочувствие — один словарь."""
    user = await _user(session)
    draft = MealDraft(title="Овсянка", items=[ItemDraft(name="овсянка")])
    for _ in range(2):
        await repo.remember_meal(session, user, draft)
    await repo.save_product(session, user, ProductDraft(name="Йогурт", brand="Село", kcal_100=86))
    await repo.bump_dictionary(session, user, kind="medication", label="Глюкофаж 850")
    await repo.save_checkin(session, user, at=NOW, score=2, symptom_labels=["сонливость"])

    by_kind = {
        kind: [e.label for e in await repo.list_dictionary(session, user, kind=kind)]
        for kind in repo.DICTIONARY_KINDS
    }
    assert by_kind["meal"] == ["Овсянка"]
    assert by_kind["item"] == ["овсянка"]
    assert by_kind["product"] == ["Село Йогурт"]
    assert by_kind["medication"] == ["Глюкофаж 850"]
    assert by_kind["symptom"] == ["сонливость"]


async def test_the_last_entry_comes_first(session):
    """Ротация: словарь открывается тем, что записано последним."""
    user = await _user(session)
    for _ in range(5):
        await repo.bump_dictionary(session, user, kind="meal", label="Кофе чёрный")
    for _ in range(2):
        await repo.bump_dictionary(session, user, kind="meal", label="Кофе с молоком")

    assert [e.label for e in await repo.list_dictionary(session, user, kind="meal")] == [
        "Кофе с молоком",
        "Кофе чёрный",
    ]
    assert [e.label for e in await repo.suggest_dictionary(session, user, "кофе")] == [
        "Кофе с молоком",
        "Кофе чёрный",
    ]
    # …и снова наверх поднимается то, что нажали только что
    await repo.bump_dictionary(session, user, kind="meal", label="Кофе чёрный")
    assert [e.label for e in await repo.list_dictionary(session, user, kind="meal")][0] == (
        "Кофе чёрный"
    )


async def test_an_item_button_carries_the_card_with_it(session):
    """Кнопка «🥄 овсянка» открывает карточку, а не просит описать порцию."""
    user = await _user(session)
    draft = MealDraft(title="Завтрак", items=[ItemDraft(name="овсянка", portion_g=200)])
    for _ in range(2):
        await repo.remember_meal(session, user, draft)
    entry = (await repo.list_dictionary(session, user, kind="item"))[0]
    assert [i["name"] for i in entry.payload["items"]] == ["овсянка"]


async def test_example_labels_take_food_only(session):
    user = await _user(session)
    for _ in range(2):
        await repo.bump_dictionary(session, user, kind="item", label="сырники")
    await repo.bump_dictionary(session, user, kind="medication", label="Глюкофаж 850")
    await repo.bump_dictionary(session, user, kind="symptom", label="сонливость")
    # лекарство и симптом примером «как записать еду» быть не могут
    assert await repo.example_labels(session, user) == ["сырники"]


async def test_example_labels_wait_for_the_second_sighting(session):
    user = await _user(session)
    await repo.bump_dictionary(session, user, kind="item", label="сырники")
    assert await repo.example_labels(session, user) == []
