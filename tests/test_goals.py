"""Цели при знакомстве: каталог, кодирование, влияние на анкету и подсказки.

`spec/onboarding.md` § Цели.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src import goals
from src.db import repo
from src.handlers import body
from src.handlers import goals as goals_handler
from src.handlers.states import GoalsFlow
from tests.test_handlers_flow import TG_ID, FakeCallback, FakeMessage


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=TG_ID, user_id=TG_ID)
    )


# ------------------------------------------------------------------ каталог

def test_the_stored_string_keeps_the_catalogue_order():
    assert goals.decode("sugar,weight") == ("weight", "sugar")
    assert goals.encode(["sugar", "weight"]) == "weight,sugar"


def test_unknown_keys_are_dropped_not_shown():
    assert goals.decode("weight,кто-то дописал руками") == ("weight",)
    assert goals.encode([]) == ""


def test_the_free_form_variant_is_shown_as_written():
    assert goals.titles(["custom"], note="хочу спать по ночам") == ["хочу спать по ночам"]
    assert goals.titles(["custom"]) == []


def test_the_target_weight_is_for_those_who_came_about_weight():
    assert goals.wants_weight_goal(["weight"])
    assert goals.wants_weight_goal(["muscle"])
    assert not goals.wants_weight_goal(["sugar", "labs"])
    # Молчание — не отказ: не назвал целей, спрашиваем как раньше.
    assert goals.wants_weight_goal([])


def test_goals_order_the_features_without_inventing_new_ones():
    order = goals.feature_order(["sugar", "labs"])
    assert order[0] == "stats"
    assert "labs" in order
    assert set(order) <= {feature for goal in goals.GOALS for feature in goal.features}


def test_a_note_is_trimmed_but_never_parsed():
    assert goals.normalize_note("  хочу   меньше  отёков ") == "хочу меньше отёков"
    assert goals.normalize_note("") is None
    assert len(goals.normalize_note("я" * 500)) == goals.NOTE_LIMIT


# ------------------------------------------------------------------ выбор

async def test_picking_toggles_and_done_saves(engine, session, state):
    card = FakeMessage()
    await goals_handler.ask_focus(card, state)
    await goals_handler.on_pick(FakeCallback(data="gl:pick:sugar", message=card), state)
    await goals_handler.on_pick(FakeCallback(data="gl:pick:labs", message=card), state)
    await goals_handler.on_pick(FakeCallback(data="gl:pick:labs", message=card), state)  # снял
    await goals_handler.on_done(FakeCallback(data="gl:done", message=card), state)

    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile.focus == "sugar"


async def test_nothing_picked_is_a_saved_answer_not_a_missing_one(engine, session, state):
    card = FakeMessage()
    await goals_handler.ask_focus(card, state)
    await goals_handler.on_done(FakeCallback(data="gl:done", message=card), state)

    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile.focus == ""  # спросили, целей не назвал


async def test_the_own_variant_is_stored_verbatim(engine, session, state):
    card = FakeMessage()
    await goals_handler.ask_focus(card, state)
    await goals_handler.on_other(FakeCallback(data="gl:other", message=card), state)
    assert await state.get_state() == GoalsFlow.note.state

    await goals_handler.on_note(FakeMessage(text="перестать заедать стресс"), state)
    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile.focus == "custom"
    assert profile.focus_note == "перестать заедать стресс"


async def test_the_picker_opens_from_the_body_card_with_the_saved_answer(engine, session, state):
    card = FakeMessage()
    await goals_handler.ask_focus(card, state)
    await goals_handler.on_pick(FakeCallback(data="gl:pick:habits", message=card), state)
    await goals_handler.on_done(FakeCallback(data="gl:done", message=card), state)

    again = FakeMessage()
    await body.on_field(FakeCallback(data="bd:field:focus", message=again), state)
    markup = again.sent[-1]["reply_markup"]
    checked = [b.text for row in markup.inline_keyboard for b in row if b.text.startswith("☑️")]
    assert checked == ["☑️ Наладить питание"]


async def test_the_body_card_shows_the_goals(engine, session, state):
    user = await repo.get_or_create_user(session, TG_ID)
    await repo.upsert_body_profile(session, user, focus="sugar,custom", focus_note="меньше отёков")
    await session.commit()

    card = FakeMessage()
    await body.cmd_body(card, state)
    assert "Цели: держать сахар в норме · меньше отёков" in card.texts[-1]


# ------------------------------------------------------------------ T064

def test_goals_reorder_a_report_without_adding_or_dropping_anything():
    """Цель меняет порядок и только его: набор разделов обязан совпасть."""
    from src.goals import report_order

    default = ("glucose", "components", "meals", "steps", "weight")
    ordered = report_order(["sport", "weight"], default)
    assert set(ordered) == set(default)
    assert ordered[0] == "weight"          # «снизить вес» — первым каталогом
    assert "steps" in ordered[:3]          # «форма и выносливость» подняла шаги


def test_no_goals_keeps_the_default_order():
    from src.goals import report_order

    default = ("meals", "glucose", "wellbeing")
    assert report_order([], default) == default
    assert report_order(["custom"], default) == default


def test_a_goal_section_the_report_does_not_have_is_ignored():
    from src.goals import report_order

    # у «Сегодня» нет раздела `components`, цель `sugar` просит его и `glucose`
    assert report_order(["sugar"], ("meals", "glucose")) == ("glucose", "meals")


def test_the_digest_puts_the_goal_first():
    from datetime import UTC, datetime

    from src.analytics.cgm_metrics import summarize
    from src.analytics.digest import KeyChange, WeeklyDigest
    from src.analytics.windows import GlucosePoint
    from src.reporting import format_weekly_digest

    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    digest = WeeklyDigest(since=now, until=now)
    digest.glucose_now = summarize([GlucosePoint(at=now, value=8.0)] * 25)
    digest.glucose_before = summarize([GlucosePoint(at=now, value=6.0)] * 25)
    digest.risen = [
        KeyChange(key="white_rice", key_type="tag", now=3.0, before=1.0, n_now=4, n_before=4,
                  kind="up")
    ]

    sugar_first = format_weekly_digest(digest, focus=["sugar"])
    assert sugar_first.index("Средний сахар") < sugar_first.index("средний подъём выше")

    # цель про еду поднимает компоненты выше сахара
    habits_first = format_weekly_digest(digest, focus=["habits"])
    assert habits_first.index("средний подъём выше") < habits_first.index("Средний сахар")
    # и ни одна строка при этом не потерялась
    assert sorted(sugar_first.split("\n")) == sorted(habits_first.split("\n"))


def test_an_empty_day_suggests_what_the_person_came_for():
    from datetime import date

    from src.reporting import format_today

    text = format_today(date(2026, 9, 14), focus=["sugar"])
    assert "показание сахара" in text
    assert "Сегодня записей пока нет." in text

    # целей нет — общая подсказка, как раньше
    plain = format_today(date(2026, 9, 14))
    assert "Пришлите фото еды или показание сахара." in plain


def test_today_orders_its_blocks_by_the_goal():
    from datetime import date, datetime

    from src.reporting import format_today

    at = datetime(2026, 9, 14, 8, 30)
    text = format_today(
        date(2026, 9, 14),
        meals=[(at, "Овсянка", 30.0)],
        readings=[(at, 5.4)],
        focus=["sugar"],
    )
    assert text.index("<b>Сахар</b>") < text.index("<b>Еда</b>")

    plain = format_today(
        date(2026, 9, 14),
        meals=[(at, "Овсянка", 30.0)],
        readings=[(at, 5.4)],
    )
    assert plain.index("<b>Еда</b>") < plain.index("<b>Сахар</b>")
