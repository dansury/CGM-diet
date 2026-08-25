"""Rotating examples in prompts: the pool moves, the personal one is rare."""

from __future__ import annotations

import pytest

from src.reporting import (
    CORRECTION_EXAMPLES,
    correction_examples,
    correction_hint,
    describe_food_retry,
    dish_example,
    food_examples,
    format_meal_draft,
    glucose_examples,
    items_example,
    macros_prompt,
    meal_edit_prompt,
    reset_examples,
)
from src.vision.schemas import ItemDraft, MealDraft

PERSONAL = ["сырники", "плов", "борщ"]


@pytest.fixture(autouse=True)
def _fresh_rotation():
    reset_examples()
    yield
    reset_examples()


def test_examples_do_not_repeat_on_the_next_prompt():
    seen = [tuple(correction_examples(1)) for _ in range(len(CORRECTION_EXAMPLES))]
    assert len(set(seen)) == len(CORRECTION_EXAMPLES)
    # ...and the pool wraps around instead of running out
    assert tuple(correction_examples(1)) == seen[0]


def test_the_personal_example_comes_no_more_often_than_every_other_time():
    rounds = [correction_examples(1, PERSONAL) for _ in range(6)]
    with_personal = [i for i, examples in enumerate(rounds) if len(examples) == 3]
    assert with_personal == [0, 2, 4]
    # третьим пунктом и с весом — «сырники 150 г», а не склонённое «убери сырники»
    assert rounds[0][2].startswith("сырники ")
    assert rounds[0][2].endswith(" г")
    assert rounds[0][:2] == list(CORRECTION_EXAMPLES[0])


def test_personal_examples_rotate_too():
    first = correction_examples(1, PERSONAL)[2]
    correction_examples(1, PERSONAL)
    second = correction_examples(1, PERSONAL)[2]
    assert first.split()[0] != second.split()[0]


def test_without_a_dictionary_only_the_common_examples_are_shown():
    assert len(correction_examples(1)) == 2
    assert len(food_examples(1)) == 1
    assert len(glucose_examples(1)) == 2


def test_rotation_is_per_user():
    # счётчик свой у каждого: чужие подсказки не сдвигают мои
    assert correction_examples(1) == correction_examples(2)
    correction_examples(1)
    assert correction_examples(1) != correction_examples(2)


def test_hints_quote_the_examples_they_are_given():
    assert correction_hint(["убери салат", "сырники 200 г"]) == (
        "Скорректировать можно текстом или голосовым: «убери салат», «сырники 200 г»."
    )
    assert "«сырники»" in describe_food_retry(["сырники"])


def test_a_card_shows_the_examples_it_is_given():
    draft = MealDraft(title="Обед", items=[ItemDraft(name="гречка", portion_g=200)])
    text = format_meal_draft(draft, examples=["убери салат", "сырники 200 г"])
    assert "«сырники 200 г»" in text


def test_the_manual_list_is_built_from_the_users_own_dishes():
    line = items_example(1, PERSONAL)
    assert line.startswith("сырники ")
    assert line.count(",") == 2
    # через раз — общий пример, а не личный
    assert "сырники" not in items_example(1, PERSONAL)


def test_one_saved_dish_is_not_enough_for_the_manual_list():
    # «сырники 150» одной строкой не показывает формат «через запятую»
    assert items_example(1, ["сырники"]) == "гречка 250, курица 100, салат 150"


def test_the_macros_prompt_names_a_dish_of_the_user():
    assert "<code>сырники 200 г б 12 ж 6 у 40 292 ккал</code>" in macros_prompt(
        dish_example(1, PERSONAL)
    )


def test_the_edit_prompt_carries_the_sample_line():
    assert "<code>рис 200, рыба 120, овощи 100</code>" in meal_edit_prompt(
        "рис 200, рыба 120, овощи 100"
    )
