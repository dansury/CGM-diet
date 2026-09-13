"""The aiogram dispatcher itself: filters, outer middleware, router order.

`test_handlers_flow.py` calls handler bodies directly with fakes and never
touches the dispatcher (`docs/bmad/06-qa-plan.md` § Известные пробелы, T043).
Here updates go through the real `Dispatcher.feed_update` with the exact
wiring from `src.bot.build_dispatcher`, against a fake `Bot` session so no
network call ever happens.

Uses the session-scoped `dispatcher` fixture (`tests/conftest.py`): every
handler router is a module-level singleton aiogram parents exactly once, so
`build_dispatcher()` can only run a single time for the whole test session.
Per-test isolation instead comes from a fresh `Bot`/fake session each time and
from resetting the in-process throttle caches that would otherwise leak
between tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from src.config import load_settings
from src.db import repo
from src.reporting import NOTIFY_INTRO


class FakeSession(BaseSession):
    """Records every outgoing Bot API call instead of hitting the network."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:
        self.calls.append(method)
        if type(method).__name__ == "SendMessage":
            return Message(
                message_id=len(self.calls),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=getattr(method, "text", "") or "",
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover - unused
        if False:
            yield b""

    @property
    def sent_texts(self) -> list[str]:
        return [m.text for m in self.calls if type(m).__name__ == "SendMessage"]


@pytest.fixture(autouse=True)
def _fresh_dispatcher_throttles():
    """The dispatcher fixture is shared; its middlewares keep in-process
    throttle caches (`reset_seen_cache`, `reset_presence_cache`) that must not
    leak between tests the way a fresh process would never see them mixed."""
    from src.handlers.presence import reset_presence_cache
    from src.handlers.user_tracking import reset_seen_cache

    reset_seen_cache()
    reset_presence_cache()


def _bot() -> tuple[Bot, FakeSession]:
    session = FakeSession()
    return Bot(token="123:test", session=session), session


def _text_update(text: str, tg_id: int, *, update_id: int = 1) -> Update:
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=Chat(id=tg_id, type="private"),
        from_user=TgUser(id=tg_id, is_bot=False, first_name="Т"),
        text=text,
    )
    return Update(update_id=update_id, message=message)


# --------------------------------------------------------- filters + fallthrough

async def test_owner_filter_answers_the_owner_and_falls_through_for_everyone_else(
    dispatcher, monkeypatch, session
):
    """`admin.router` is owner-only; a stranger's same command gets silence
    (`spec/models.md`), not an error — and outer middleware still runs on them.
    """
    OWNER_ID, STRANGER_ID = 900, 901
    monkeypatch.setenv("OWNER_TG_IDS", str(OWNER_ID))
    load_settings(refresh=True)
    assert load_settings().is_owner(OWNER_ID)

    bot, fake = _bot()
    await dispatcher.feed_update(bot, _text_update("/models", OWNER_ID, update_id=1))
    assert any("Модели по слотам" in text for text in fake.sent_texts)

    fake.calls.clear()
    await dispatcher.feed_update(bot, _text_update("/models", STRANGER_ID, update_id=2))
    # No admin reply leaked to the stranger; the only traffic is
    # UserTrackingMiddleware's owner DM about a first-time visitor — proof
    # that outer middleware ran even though no router answered them.
    assert not any("Модели по слотам" in text for text in fake.sent_texts)
    assert any("Новый пользователь" in text for text in fake.sent_texts)

    assert await repo.get_user(session, STRANGER_ID) is not None

    await bot.session.close()


async def test_outer_presence_middleware_runs_even_when_no_router_answers(
    dispatcher, monkeypatch, session
):
    """`PresenceMiddleware` sits on `dispatcher.update` — above routing — so a
    silently-dropped update (owner-only command from a stranger) still counts
    as an appearance for sleep tracking (`spec/sleep.md`).
    """
    monkeypatch.setenv("OWNER_TG_IDS", "900")
    load_settings(refresh=True)

    watcher = await repo.get_or_create_user(session, 950)
    watcher.sleep_presence_enabled = True
    await session.commit()

    bot, fake = _bot()
    await dispatcher.feed_update(bot, _text_update("/models", 950, update_id=1))

    assert fake.sent_texts == []  # not the owner: nothing answered
    assert await repo.load_presence(session, watcher) != []

    await bot.session.close()


# ------------------------------------------------------------------ router order

async def test_a_specific_command_wins_over_the_catch_all_text_router(dispatcher, session):
    """`notify.router` is registered well before `intake.router`'s catch-all
    (`F.text & ~F.text.startswith("/")`); `/notify` must reach the former, not
    be swallowed as a free-text food note by the latter.
    """
    bot, fake = _bot()
    await dispatcher.feed_update(bot, _text_update("/notify", 700, update_id=1))
    assert fake.sent_texts == [NOTIFY_INTRO]

    await bot.session.close()
