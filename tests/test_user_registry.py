"""Owner registry: upserts, block state, and the `/users` listing text."""

from __future__ import annotations

from datetime import UTC, datetime

from src.db import repo
from src.handlers import admin_panel
from src.vision.schemas import GlucoseDraft, ItemDraft, MealDraft

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


async def test_touch_user_reports_new_then_refreshes_profile(session):
    user, is_new = await repo.touch_user(session, 42, username="ivan", first_name="Иван")
    assert is_new is True
    assert user.last_seen_at is not None

    again, is_new = await repo.touch_user(session, 42, username="ivan_new", first_name="Иван")
    assert is_new is False
    assert again.id == user.id
    assert again.username == "ivan_new"


async def test_touch_user_clears_the_block_flag(session):
    await repo.touch_user(session, 7)
    blocked = await repo.set_user_blocked(session, 7, True)
    assert blocked is not None and blocked.blocked_at is not None
    assert (await repo.count_users(session)) == (1, 1)

    back, _ = await repo.touch_user(session, 7)
    assert back.blocked_at is None
    assert (await repo.count_users(session)) == (1, 0)


async def test_set_blocked_on_unknown_user_is_a_noop(session):
    assert await repo.set_user_blocked(session, 999, True) is None


async def test_user_activity_counts_meals_and_readings(session):
    user, _ = await repo.touch_user(session, 1)
    other, _ = await repo.touch_user(session, 2)
    await repo.save_meal(
        session,
        user,
        MealDraft(title="Обед", items=[ItemDraft(name="Рис", portion_g=100, kcal=130)]),
        eaten_at=NOW,
    )
    await repo.save_glucose(session, user, [GlucoseDraft(measured_at=NOW, value_mmol=5.4)])
    await session.flush()

    activity = await repo.user_activity(session)
    meals, readings, last = activity[user.id]
    assert (meals, readings) == (1, 1)
    assert last == NOW
    assert other.id not in activity


async def test_render_users_lists_the_registry(engine):
    factory = __import__("src.db.engine", fromlist=["x"]).create_sessionmaker(engine)
    async with factory() as s:
        user, _ = await repo.touch_user(s, 42, username="ivan", first_name="Иван")
        await repo.set_user_blocked(s, 42, True)
        await s.commit()

    text = await admin_panel.render_users()
    assert "Пользователи (1)" in text
    assert "заблокировали: 1" in text
    assert "@ivan" in text
    assert "🚫" in text


async def test_render_users_without_anyone(engine):
    text = await admin_panel.render_users()
    assert "Пока никого" in text


# ---------------------------------------------------------------- alerts


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class FakeChat:
    def __init__(self, type: str = "private") -> None:
        self.type = type


class FakeFrom:
    def __init__(self, id: int = 42, username: str | None = "ivan", first_name: str | None = "Иван"):
        self.id = id
        self.username = username
        self.first_name = first_name
        self.is_bot = False


class FakeMessage:
    def __init__(self, chat_type: str = "private", user: FakeFrom | None = None) -> None:
        self.chat = FakeChat(chat_type)
        self.from_user = user or FakeFrom()


class FakeMember:
    def __init__(self, status: str) -> None:
        self.status = status


class FakeChatMemberUpdated:
    def __init__(self, status: str, bot: FakeBot) -> None:
        self.new_chat_member = FakeMember(status)
        self.from_user = FakeFrom()
        self.chat = FakeChat()
        self.bot = bot


async def test_new_user_alerts_the_owner_once(engine, monkeypatch):
    from src.handlers import user_tracking

    user_tracking.reset_seen_cache()
    monkeypatch.setattr(user_tracking, "_owner_ids", lambda: (777,))
    bot = FakeBot()

    await user_tracking.track(FakeMessage(), bot)
    await user_tracking.track(FakeMessage(), bot)

    assert len(bot.sent) == 1
    owner_id, text = bot.sent[0]
    assert owner_id == 777
    assert "Новый пользователь" in text and "@ivan" in text


async def test_group_chats_are_not_tracked(engine, monkeypatch):
    from src.handlers import user_tracking

    user_tracking.reset_seen_cache()
    monkeypatch.setattr(user_tracking, "_owner_ids", lambda: (777,))
    bot = FakeBot()

    await user_tracking.track(FakeMessage(chat_type="supergroup"), bot)

    assert bot.sent == []


async def test_block_and_unblock_are_written_and_announced(engine, monkeypatch):
    from src.db.engine import create_sessionmaker
    from src.handlers import user_tracking

    user_tracking.reset_seen_cache()
    monkeypatch.setattr(user_tracking, "_owner_ids", lambda: (777,))
    bot = FakeBot()
    await user_tracking.track(FakeMessage(), bot)

    await user_tracking.on_private_my_chat_member(FakeChatMemberUpdated("kicked", bot))
    factory = create_sessionmaker(engine)
    async with factory() as s:
        assert (await repo.count_users(s)) == (1, 1)
    assert "заблокировал бота" in bot.sent[-1][1]

    await user_tracking.on_private_my_chat_member(FakeChatMemberUpdated("member", bot))
    async with factory() as s:
        assert (await repo.count_users(s)) == (1, 0)
    assert "разблокировал бота" in bot.sent[-1][1]
