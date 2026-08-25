"""Подсказки о неиспользованных возможностях: выбор, отказ, скрытое меню."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src import features
from src.db import repo
from src.handlers import features as features_handler
from src.keyboards import main_menu
from src.scheduler import run_feature_hints

TG_ID = 515151
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ выбор

def test_the_first_hint_is_the_first_unused_feature():
    assert features.pick_hint({}, {}).key == "plate"


def test_a_feature_already_used_is_never_offered():
    counts = {"labs": 2}
    states = {"plate": features.FeatureState(status=features.STATUS_ACCEPTED)}
    assert features.pick_hint(counts, states).key == "workout"


def test_two_messages_per_feature_is_the_whole_budget():
    states = {"plate": features.FeatureState(status=features.STATUS_SHOWN, shown=2)}
    assert features.pick_hint({}, states).key == "labs"


def test_a_declined_feature_never_comes_back():
    states = {
        key: features.FeatureState(status=features.STATUS_DECLINED)
        for key in features.BY_KEY
    }
    assert features.pick_hint({}, states) is None
    assert features.hidden_keys(states) == set(features.BY_KEY)


def test_features_without_a_table_are_tracked_by_an_explicit_mark():
    graph = features.BY_KEY["graph"]
    assert not features.is_used(graph, {"meals": 10}, features.FeatureState())
    assert features.is_used(graph, {}, features.FeatureState(used=True))


def test_a_hidden_feature_leaves_the_menu_but_not_the_bot():
    buttons = {b.text for row in main_menu({"workout"}).keyboard for b in row}
    assert "🏃 Тренировка" not in buttons
    assert "🍽 Записать еду" in buttons


# ------------------------------------------------------------------ фейки

@dataclass
class FakeUser:
    id: int = TG_ID


@dataclass
class FakeChat:
    id: int = TG_ID


@dataclass
class FakeMessage:
    text: str | None = None
    chat: FakeChat = field(default_factory=FakeChat)
    from_user: FakeUser = field(default_factory=FakeUser)
    sent: list[dict] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: Any) -> FakeMessage:
        self.sent.append({"text": text, **kwargs})
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> FakeMessage:
        self.sent.append({"edited": text, **kwargs})
        return self

    @property
    def texts(self) -> list[str]:
        return [s.get("text") or s.get("edited") or "" for s in self.sent]


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.commands: list[tuple[list[str], Any]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})

    async def set_my_commands(self, commands, scope=None) -> None:
        self.commands.append(([c.command for c in commands], scope))


@dataclass
class FakeCallback:
    data: str
    message: FakeMessage
    from_user: FakeUser = field(default_factory=FakeUser)
    bot: FakeBot | None = None
    answers: list[str | None] = field(default_factory=list)

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append(text)


@pytest.fixture
async def user(engine, session):
    return await repo.get_or_create_user(session, TG_ID)


# ------------------------------------------------------------------ поток

async def test_the_hint_is_sent_once_and_counted(engine, session, user):
    bot = FakeBot()
    key = await features_handler.maybe_send_hint(bot, TG_ID)
    assert key == "plate"
    assert "Гарвардская тарелка" in bot.messages[0]["text"]

    await session.refresh(user)
    states = await repo.feature_states(session, user)
    assert states["plate"].shown == 1
    assert user.last_hint_at is not None


async def test_the_third_message_about_one_feature_never_happens(engine, session, user):
    bot = FakeBot()
    assert await features_handler.maybe_send_hint(bot, TG_ID) == "plate"
    assert await features_handler.maybe_send_hint(bot, TG_ID) == "plate"
    assert await features_handler.maybe_send_hint(bot, TG_ID) == "labs"


async def test_declining_hides_the_feature_from_both_menus(engine, session, user):
    bot = FakeBot()
    callback = FakeCallback(data="feat:no:workout", message=FakeMessage(), bot=bot)
    await features_handler.on_decline(callback)

    await session.refresh(user)
    assert await repo.hidden_features(session, user) == {"workout"}
    markup = callback.message.sent[-1]["reply_markup"]
    assert "🏃 Тренировка" not in {b.text for row in markup.keyboard for b in row}
    listed, scope = bot.commands[-1]
    assert "workout" not in listed
    assert scope.chat_id == TG_ID


async def test_a_declined_feature_is_listed_in_hidden_and_can_come_back(engine, session, user):
    bot = FakeBot()
    await features_handler.on_decline(
        FakeCallback(data="feat:no:meds", message=FakeMessage(), bot=bot)
    )

    listing = FakeMessage()
    await features_handler.cmd_hidden(listing)
    assert "Лекарства" in listing.texts[-1]

    await features_handler.on_restore(
        FakeCallback(data="feat:show:meds", message=FakeMessage(), bot=bot)
    )
    await session.refresh(user)
    assert await repo.hidden_features(session, user) == set()


async def test_accepting_keeps_the_feature_and_stops_the_hints(engine, session, user):
    bot = FakeBot()
    await features_handler.on_accept(FakeCallback(data="feat:ok:plate", message=FakeMessage()))
    await session.refresh(user)
    assert await repo.hidden_features(session, user) == set()
    assert await features_handler.maybe_send_hint(bot, TG_ID) == "labs"


async def test_hidden_is_empty_by_default(engine, session, user):
    message = FakeMessage()
    await features_handler.cmd_hidden(message)
    assert "Скрытых возможностей нет" in message.texts[-1]


# ------------------------------------------------------------------ /start

async def test_start_tells_nothing_about_features_and_starts_the_week(engine, session):
    """При первом знакомстве — только приветствие и анкета, без подсказок."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from src.handlers import common
    from tests.test_handlers_flow import TG_ID as FLOW_TG_ID
    from tests.test_handlers_flow import FakeMessage

    message = FakeMessage()
    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=FLOW_TG_ID, user_id=FLOW_TG_ID),
    )
    await common.cmd_start(message, state)

    assert all("возможность" not in text for text in message.texts)
    started = await repo.get_or_create_user(session, FLOW_TG_ID)
    assert started.last_hint_at is not None  # отсчёт недели пошёл со /start


async def test_deferring_hints_never_moves_an_existing_countdown(engine, session, user):
    user.last_hint_at = NOW
    await repo.defer_hints(session, user, at=NOW + timedelta(days=3))
    assert user.last_hint_at == NOW


# ------------------------------------------------------------------ расписание

async def test_the_weekly_tick_writes_once_a_week(engine, session, user):
    user.onboarded = True
    await session.commit()
    bot = FakeBot()

    assert await run_feature_hints(bot, now=NOW) == 1
    assert await run_feature_hints(bot, now=NOW + timedelta(days=1)) == 0
    assert await run_feature_hints(bot, now=NOW + timedelta(days=8)) == 1


async def test_the_tick_keeps_quiet_at_night(engine, session, user):
    user.onboarded = True
    await session.commit()
    bot = FakeBot()
    assert await run_feature_hints(bot, now=NOW.replace(hour=3)) == 0
    assert bot.messages == []
