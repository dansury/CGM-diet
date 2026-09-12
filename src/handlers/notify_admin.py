"""Owner's notification editor: `/notify_admin` (`spec/notifications.md` § Админка).

What is written here is the *starting* setting for everyone; each person can
then move the time, switch on «умное» or turn the notification off entirely.
Switching a notification off here removes it for everyone — that is the owner
deleting it, not a default to be overridden.

Private chat only, `OWNER_TG_IDS` only, same gate as `handlers/admin_panel.py`:
a stranger's update simply falls through, so the bot never leaks that these
commands exist.
"""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.analytics import notify as notify_math
from src.config import load_settings
from src.db import repo
from src.handlers.deps import session_scope
from src.handlers.states import NotifyAdminFlow
from src.keyboards import cancel_only, notification_admin_card, notification_admin_list
from src.logging_setup import get_logger

router = Router(name="notify_admin")
log = get_logger("handlers.notify_admin")


def _is_owner(user_id: int | None) -> bool:
    return user_id is not None and load_settings().is_owner(user_id)


owner_filter = F.func(lambda event: _is_owner(getattr(event.from_user, "id", None)))
router.message.filter(F.chat.type == "private", owner_filter)
router.callback_query.filter(owner_filter)

FIELD_PROMPTS = {
    "title": "Новый заголовок уведомления:",
    "body": "Новый текст уведомления:",
    "times": "Время через запятую, например <code>08:30, 13:30, 19:00</code>:",
    "mode": (
        "Режим по умолчанию: <code>fixed</code> — по времени выше, "
        "<code>smart</code> — умное, <code>off</code> — не слать по умолчанию."
    ),
}
INTRO = (
    "🔔 <b>Уведомления — настройка для всех</b>\n\n"
    "Это исходное состояние: каждый человек может поставить своё время, "
    "включить «умное» или выключить напоминание у себя (/notify). "
    "🔕 — выключено у всех."
)


@router.message(Command("notify_admin"))
async def cmd_notify_admin(message: Message) -> None:
    await _render_list(message)


async def _render_list(message: Message, *, edit: bool = False) -> None:
    async with session_scope() as session:
        await repo.seed_notifications(session)
        rows = await repo.list_notification_templates(session)
        items = [(row.code, row.title, bool(row.enabled)) for row in rows]
    markup = notification_admin_list(items)
    if edit:
        await message.edit_text(INTRO, reply_markup=markup)
    else:
        await message.answer(INTRO, reply_markup=markup)


@router.callback_query(F.data == "ntfa:list")
async def on_list(callback: CallbackQuery) -> None:
    await callback.answer()
    await _render_list(callback.message, edit=True)


@router.callback_query(F.data.startswith("ntfa:open:"))
async def on_open(callback: CallbackQuery) -> None:
    await callback.answer()
    await _render_card(callback.message, callback.data.rsplit(":", 1)[1], edit=True)


async def _render_card(message: Message, code: str, *, edit: bool = False) -> None:
    async with session_scope() as session:
        row = await repo.get_notification_template(session, code)
        if row is None:
            await message.answer("Такого уведомления нет.")
            return
        card = (
            f"<b>{escape(row.title)}</b>\n"
            f"{escape(row.body or '—')}\n\n"
            f"код: <code>{escape(row.code)}</code>\n"
            f"время: {escape(row.times or '—')}\n"
            f"режим: {escape(row.mode)}\n"
            f"по нажатию: {escape(row.action)}\n"
            f"сигналы для «умного»: {escape(row.signal or '—')}\n"
            f"упреждение: {row.lead_min} мин\n"
            f"состояние: {'включено' if row.enabled else 'выключено у всех'}"
        )
        enabled = bool(row.enabled)
    markup = notification_admin_card(code, enabled)
    if edit:
        await message.edit_text(card, reply_markup=markup)
    else:
        await message.answer(card, reply_markup=markup)


@router.callback_query(F.data.startswith("ntfa:set:"))
async def on_set(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, code, field = callback.data.split(":", 3)
    if field not in FIELD_PROMPTS:
        await callback.answer()
        return
    await state.set_state(NotifyAdminFlow.editing)
    await state.update_data({"ntfa_code": code, "ntfa_field": field})
    await callback.answer()
    await callback.message.answer(FIELD_PROMPTS[field], reply_markup=cancel_only())


@router.message(NotifyAdminFlow.editing)
async def on_value_typed(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = str(data.get("ntfa_code") or "")
    field = str(data.get("ntfa_field") or "")
    value = (message.text or "").strip()
    if field == "times":
        times = notify_math.parse_times(value)
        if not times:
            await message.answer(
                "Не разобрал время. Пример: <code>08:30, 19:00</code>",
                reply_markup=cancel_only(),
            )
            return
        value = ",".join(times)
    if field == "mode":
        value = value.lower()
        if value not in notify_math.MODES:
            await message.answer(
                "Только <code>fixed</code>, <code>smart</code> или <code>off</code>.",
                reply_markup=cancel_only(),
            )
            return
    if field in {"title", "body"} and not value:
        await message.answer("Пустой текст не сохраняю.", reply_markup=cancel_only())
        return
    await state.clear()
    async with session_scope() as session:
        await repo.upsert_notification_template(session, code, **{field: value})
    await message.answer("Сохранено.")
    await _render_card(message, code)


@router.callback_query(F.data.startswith("ntfa:toggle:"))
async def on_toggle(callback: CallbackQuery) -> None:
    code = callback.data.rsplit(":", 1)[1]
    async with session_scope() as session:
        row = await repo.get_notification_template(session, code)
        if row is None:
            await callback.answer("Такого уведомления нет")
            return
        await repo.upsert_notification_template(session, code, enabled=not row.enabled)
    await callback.answer("Готово")
    await _render_card(callback.message, code, edit=True)


@router.callback_query(F.data.startswith("ntfa:del:"))
async def on_delete(callback: CallbackQuery) -> None:
    code = callback.data.rsplit(":", 1)[1]
    async with session_scope() as session:
        await repo.delete_notification_template(session, code)
    await callback.answer("Удалено вместе с настройками людей")
    await _render_list(callback.message, edit=True)


@router.callback_query(F.data == "ntfa:new")
async def on_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NotifyAdminFlow.creating)
    await callback.answer()
    await callback.message.answer(
        "Новое уведомление. Пришлите одной строкой:\n"
        "<code>код | Заголовок | Текст | 08:30,19:00</code>",
        reply_markup=cancel_only(),
    )


@router.message(NotifyAdminFlow.creating)
async def on_new_typed(message: Message, state: FSMContext) -> None:
    parts = [chunk.strip() for chunk in (message.text or "").split("|")]
    code = _slug(parts[0] if parts else "")
    if not code or len(parts) < 2:
        await message.answer(
            "Нужен хотя бы код и заголовок: <code>water | Пора попить</code>",
            reply_markup=cancel_only(),
        )
        return
    times = ",".join(notify_math.parse_times(parts[3])) if len(parts) > 3 else ""
    await state.clear()
    async with session_scope() as session:
        await repo.upsert_notification_template(
            session,
            code,
            title=parts[1],
            body=parts[2] if len(parts) > 2 else "",
            times=times,
            mode="fixed",
            action="camera",
            enabled=True,
        )
    await message.answer("Создано.")
    await _render_card(message, code)


#: транслитерация для кода: он ездит в callback_data и в push-сообщении
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _slug(raw: str) -> str:
    """Латинский код: кириллица транслитерируется, а не выбрасывается."""
    lowered = "".join(_TRANSLIT.get(ch, ch) for ch in raw.strip().lower())
    cleaned = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "_-")) else "-" for ch in lowered)
    return cleaned.strip("-")[:32]


__all__ = ["cmd_notify_admin", "router"]
