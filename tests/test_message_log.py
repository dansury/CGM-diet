"""Журнал переписки и `/last_msg_…` (`spec/bot.md` § Журнал переписки)."""

from __future__ import annotations

from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.db import repo
from src.handlers import admin_panel, message_log
from tests.test_handlers_flow import TG_ID

BUTTONS = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="🩸 Записать сахар", callback_data="sg:log")]]
)


class FakeChat:
    def __init__(self, type: str = "private") -> None:
        self.type = type


class FakeFrom:
    def __init__(self, id: int = TG_ID) -> None:
        self.id = id
        self.username = "ivan"
        self.first_name = "Иван"
        self.is_bot = False


class FakeIncoming:
    """Ровно те поля, которые читает `write_incoming`."""

    def __init__(self, *, text=None, caption=None, photo=None, voice=None,
                 document=None, chat_type="private") -> None:
        self.text = text
        self.caption = caption
        self.photo = photo
        self.voice = voice
        self.audio = None
        self.document = document
        self.chat = FakeChat(chat_type)
        self.from_user = FakeFrom()


class FakeCallbackEvent:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = FakeIncoming(text="карточка")
        self.from_user = FakeFrom()


async def _passthrough(event, data):
    return "handled"


# ---------------------------------------------------------------- запись


async def test_incoming_text_and_photo_are_written(engine, session):
    await message_log.write_incoming(FakeIncoming(text="привет"))
    await message_log.write_incoming(FakeIncoming(photo=["file"], caption="обед"))

    user = await repo.get_user(session, TG_ID)
    rows = await repo.last_messages(session, user)
    assert [(row.direction, row.kind, row.text) for row in rows] == [
        ("in", "photo", "обед"),
        ("in", "text", "привет"),
    ]


async def test_a_callback_is_written_as_its_data(engine, session):
    await message_log.write_incoming(FakeCallbackEvent("meal:ok"))

    user = await repo.get_user(session, TG_ID)
    rows = await repo.last_messages(session, user)
    assert (rows[0].kind, rows[0].text) == ("callback", "meal:ok")


async def test_group_chats_stay_out_of_the_log(engine, session):
    await message_log.write_incoming(FakeIncoming(text="в группе", chat_type="group"))

    user = await repo.get_user(session, TG_ID)
    assert user is None or await repo.last_messages(session, user) == []


async def test_outgoing_messages_keep_their_buttons(engine, session):
    sent = SendMessage(chat_id=TG_ID, text="✅ Записано", reply_markup=BUTTONS)

    async def make_request(bot, method):
        return "ok"

    assert await message_log.LogOutgoingMiddleware()(make_request, None, sent) == "ok"

    user = await repo.get_user(session, TG_ID)
    row = (await repo.last_messages(session, user))[0]
    assert (row.direction, row.kind, row.text) == ("out", "text", "✅ Записано")
    assert row.buttons == [[{"t": "🩸 Записать сахар", "cb": "sg:log"}]]


async def test_a_photo_is_a_type_mark_and_a_caption_only(engine, session):
    await message_log.write_outgoing(SendPhoto(chat_id=TG_ID, photo="file-id", caption="график"))

    user = await repo.get_user(session, TG_ID)
    row = (await repo.last_messages(session, user))[0]
    assert (row.kind, row.text) == ("photo", "график")


async def test_group_messages_are_not_someones_conversation(engine, session):
    await message_log.write_outgoing(SendMessage(chat_id=-100500, text="в канал"))
    assert await repo.find_user(session, "-100500") is None


async def test_a_failing_log_line_never_eats_the_update(engine, session, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("бах")

    monkeypatch.setattr(message_log, "write_incoming", boom)
    message = FakeIncoming(text="привет")
    # middleware пропускает только настоящие типы aiogram, поэтому проверяем,
    # что сбой записи не мешает: обработчик всё равно вызван
    assert await message_log.LogIncomingMiddleware()(_passthrough, message, {}) == "handled"

    async def outgoing_boom(*args, **kwargs):
        raise RuntimeError("бах")

    monkeypatch.setattr(message_log, "write_outgoing", outgoing_boom)

    async def make_request(bot, method):
        return "ok"

    sent = SendMessage(chat_id=TG_ID, text="✅ Записано")
    assert await message_log.LogOutgoingMiddleware()(make_request, None, sent) == "ok"


async def test_the_log_keeps_only_the_last_rows(engine, session):
    user = await repo.get_or_create_user(session, TG_ID)
    for index in range(repo.LOG_KEEP + 5):
        await repo.log_message(session, user, direction="in", kind="text", text=str(index))
    await session.commit()

    rows = await repo.last_messages(session, user, limit=repo.LOG_KEEP + 10)
    assert len(rows) == repo.LOG_KEEP
    assert rows[0].text == str(repo.LOG_KEEP + 4)


async def test_erasure_takes_the_conversation_with_it(engine, session):
    user = await repo.get_or_create_user(session, TG_ID)
    await repo.log_message(session, user, direction="in", kind="text", text="привет")
    await session.commit()

    await repo.delete_user_data(session, user)
    await session.commit()
    assert await repo.last_messages(session, user) == []


# ---------------------------------------------------------------- поиск и вывод


async def test_a_user_is_found_by_id_and_by_username(engine, session):
    user, _ = await repo.touch_user(session, TG_ID, username="Ivan", first_name="Иван")
    await session.commit()

    assert (await repo.find_user(session, str(TG_ID))).id == user.id
    assert (await repo.find_user(session, "@ivan")).id == user.id  # регистр не важен
    assert await repo.find_user(session, "никто") is None


def test_a_bare_number_is_the_lookup_not_the_count():
    assert admin_panel._parse_last_msg("5402655420") == ("5402655420", 10)
    assert admin_panel._parse_last_msg("ivan 5") == ("ivan", 5)
    assert admin_panel._parse_last_msg("ivan 900")[1] == admin_panel.LAST_MSG_MAX


async def test_last_msg_shows_both_sides_in_order(engine, session):
    user, _ = await repo.touch_user(session, TG_ID, username="ivan", first_name="Иван")
    await repo.log_message(session, user, direction="in", kind="text", text="что мне съесть")
    await repo.log_message(
        session,
        user,
        direction="out",
        kind="text",
        text="Записал",
        buttons=[[{"t": "🩸 Записать сахар", "cb": "sg:log"}]],
    )
    await session.commit()

    text = await admin_panel.render_last_msg("ivan", 10)
    assert "Иван" in text
    assert text.index("что мне съесть") < text.index("Записал")   # хронологически
    assert "➡️" in text and "⬅️" in text
    assert "sg:log" in text                                        # кнопки видно


async def test_last_msg_says_plainly_when_there_is_nothing(engine, session):
    await repo.touch_user(session, TG_ID, username="ivan", first_name="Иван")
    await session.commit()
    assert "Переписки нет" in await admin_panel.render_last_msg("ivan", 10)


async def test_last_msg_on_a_stranger_does_not_pretend(engine, session):
    assert "не найден" in await admin_panel.render_last_msg("никто", 10)
