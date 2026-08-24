"""БЖУ, введённые пользователем: разбор, хранение, подстановка.

Тихая ошибка здесь стоит дорого: подставленные не в ту порцию числа выглядят
как измерение (см. `spec/dictionary.md` § Память БЖУ).
"""

from __future__ import annotations

from src.db import repo
from src.ingest.correction import apply_meal_correction
from src.ingest.nutrition import Remembered, apply_memory, match_memory, per_100
from src.vision.schemas import ItemDraft, MealDraft, meal_from_dict, meal_to_dict


def _draft() -> MealDraft:
    return MealDraft(
        title="Гречка с курицей",
        items=[
            ItemDraft(name="гречка", portion_g=200, kcal=220, protein_g=8, fat_g=3,
                      carbs_g=42, estimated=True),
            ItemDraft(name="курица", portion_g=150, kcal=285, protein_g=37, fat_g=13,
                      carbs_g=1, estimated=True),
        ],
    )


# --------------------------------------------------------------- разбор правки

def test_macros_are_read_from_a_correction():
    result = apply_meal_correction(_draft(), "гречка б 7 ж 2 у 45")
    item = result.draft.items[0]
    assert (item.protein_g, item.fat_g, item.carbs_g) == (7.0, 2.0, 45.0)
    assert item.macros_source == "user"
    assert item.estimated is False
    assert [c.kind for c in result.changes] == ["macros"]
    assert "Б 7" in result.changes[0].describe()


def test_macros_win_over_the_portion_rule():
    # «гречка ... 45» would otherwise be read as «гречка — 45 г»
    result = apply_meal_correction(_draft(), "гречка белки 7 жиры 2 углеводы 45")
    assert result.draft.items[0].portion_g == 200


def test_kcal_may_trail_the_number_and_a_portion_may_ride_along():
    result = apply_meal_correction(_draft(), "гречка 250 г б 9 ж 3 у 52 300 ккал")
    item = result.draft.items[0]
    assert item.portion_g == 250
    assert item.kcal == 300
    assert item.protein_g == 9


def test_kcal_is_derived_from_the_typed_macros_when_not_stated():
    result = apply_meal_correction(_draft(), "гречка б 10 ж 2 у 40")
    assert result.draft.items[0].kcal == round(10 * 4 + 2 * 9 + 40 * 4)


def test_a_plain_portion_correction_is_still_a_portion():
    result = apply_meal_correction(_draft(), "гречки было 250")
    assert [c.kind for c in result.changes] == ["portion"]
    assert result.draft.items[0].portion_g == 250


def test_macros_for_a_single_item_need_no_name():
    draft = MealDraft(title="Овсянка", items=[ItemDraft(name="овсянка", portion_g=100)])
    result = apply_meal_correction(draft, "б 5 ж 3 у 20")
    assert result.draft.items[0].macros_source == "user"


def test_macros_survive_the_fsm_round_trip():
    result = apply_meal_correction(_draft(), "гречка б 7 ж 2 у 45")
    restored = meal_from_dict(meal_to_dict(result.draft))
    assert restored.items[0].macros_source == "user"


def test_an_old_stored_draft_without_the_new_field_still_loads():
    payload = {"title": "x", "items": [{"name": "гречка", "portion_g": 100}]}
    assert meal_from_dict(payload).items[0].macros_source == ""


# --------------------------------------------------------------- подстановка

def test_per_100_uses_the_portion_as_the_basis():
    item = ItemDraft(name="гречка", portion_g=200, protein_g=8, fat_g=2, carbs_g=40)
    stored = per_100(item)
    assert (stored.protein_g, stored.fat_g, stored.carbs_g) == (4.0, 1.0, 20.0)
    assert stored.portion_g == 200


def test_numbers_without_a_portion_are_read_as_per_100_g():
    stored = per_100(ItemDraft(name="сыр", protein_g=24, fat_g=27))
    assert (stored.protein_g, stored.fat_g) == (24.0, 27.0)
    assert stored.portion_g is None


def test_memory_is_rescaled_to_the_current_portion():
    draft = MealDraft(items=[ItemDraft(name="гречка отварная", portion_g=300,
                                       protein_g=1, estimated=True)])
    filled = apply_memory(draft, {"гречка": Remembered(protein_g=4, fat_g=1, carbs_g=20)})
    item = draft.items[0]
    assert filled == ["гречка отварная"]
    assert (item.protein_g, item.fat_g, item.carbs_g) == (12.0, 3.0, 60.0)
    assert item.macros_source == "memory"
    assert item.estimated is False  # ваши числа — не оценка
    assert "сохранённых" in draft.notes


def test_memory_supplies_the_portion_when_the_weight_is_unknown():
    draft = MealDraft(items=[ItemDraft(name="гречка")])
    apply_memory(draft, {"гречка": Remembered(protein_g=4, carbs_g=20, portion_g=180)})
    item = draft.items[0]
    assert item.portion_g == 180
    assert item.carbs_g == 36.0
    assert item.kcal == round(4 * 1.8 * 4 + 20 * 1.8 * 4)


def test_what_the_user_just_typed_beats_what_was_remembered():
    draft = MealDraft(items=[ItemDraft(name="гречка", portion_g=100, protein_g=9,
                                       macros_source="user")])
    assert apply_memory(draft, {"гречка": Remembered(protein_g=4)}) == []
    assert draft.items[0].protein_g == 9


def test_memory_is_matched_by_name_not_by_identity():
    memory = {"гречка": Remembered(protein_g=4)}
    assert match_memory("Гречка отварная", memory) is not None
    assert match_memory("макароны", memory) is None


# --------------------------------------------------------------- слой repo

async def test_remembered_macros_round_trip(session):
    user = await repo.get_or_create_user(session, 501)
    draft = _draft()
    draft.items[0].protein_g = 8
    draft.items[0].macros_source = "user"
    saved = await repo.remember_meal_macros(session, user, draft)
    assert saved == ["гречка"]

    memory = await repo.load_nutrition_memory(session, user, ["гречка"])
    assert memory["гречка"].protein_g == 4.0      # 8 г на 200 г → 4 г на 100 г
    assert memory["гречка"].portion_g == 200
    # ничего чужого не подтянулось
    assert await repo.load_nutrition_memory(session, user, ["макароны"]) == {}


async def test_re_entering_macros_overwrites_the_previous_value(session):
    user = await repo.get_or_create_user(session, 502)
    first = ItemDraft(name="гречка", portion_g=100, protein_g=4, macros_source="user")
    await repo.remember_meal_macros(session, user, MealDraft(items=[first]))
    second = ItemDraft(name="гречка", portion_g=100, protein_g=9, macros_source="user")
    await repo.remember_meal_macros(session, user, MealDraft(items=[second]))
    memory = await repo.load_nutrition_memory(session, user)
    assert memory["гречка"].protein_g == 9.0


async def test_erasure_takes_the_remembered_macros_with_it(session):
    user = await repo.get_or_create_user(session, 503)
    item = ItemDraft(name="гречка", portion_g=100, protein_g=4, macros_source="user")
    await repo.remember_meal_macros(session, user, MealDraft(items=[item]))
    await repo.delete_user_data(session, user)
    assert await repo.load_nutrition_memory(session, user) == {}


# --------------------------------------------------------------- ввод «сразу с БЖУ»

def test_split_macros_separates_the_food_from_the_numbers():
    from src.ingest.correction import split_macros

    assert split_macros("овсянка 200 г б 12 ж 6 у 40") == (
        "овсянка 200 г",
        "овсянка 200 г б 12 ж 6 у 40",
    )


def test_split_macros_leaves_a_plain_description_alone():
    from src.ingest.correction import split_macros

    assert split_macros("овсянка с бананом") == ("овсянка с бананом", "")


def test_split_macros_keeps_the_dishes_that_carry_no_numbers():
    from src.ingest.correction import split_macros

    food, macros = split_macros("гречка 250, курица б 30 ж 5 у 0")
    assert food == "гречка 250, курица"
    assert macros == "курица б 30 ж 5 у 0"


def test_a_portion_alone_is_not_read_as_macros():
    from src.ingest.correction import split_macros

    assert split_macros("гречка 250 г") == ("гречка 250 г", "")
