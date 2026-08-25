"""Personal dictionary: what this user records over and over, one tap away.

A dish seen twice and a medication photographed once become buttons. Typing
the first characters offers them before any model is called — the common case
(«то же, что вчера») costs nothing and answers instantly.
See `spec/dictionary.md`.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db import repo
from src.handlers.deps import local_now, session_scope, to_utc
from src.handlers.features import mark_used, menu_of
from src.keyboards import KIND_TABS, dictionary_page, dictionary_suggestions
from src.logging_setup import get_logger
from src.vision.schemas import MealDraft, meal_from_dict

router = Router(name="dictionary")
log = get_logger("handlers.dictionary")

PAGE_SIZE = 12
SUGGEST_LIMIT = 6
MIN_PREFIX = 2

EMPTY_TEXT = (
    "⭐️ <b>Личный словарь</b>\n\n"
    "Пока пусто. Блюдо и его составляющие попадают сюда, когда встречаются "
    "<b>во второй раз</b>, а упаковка, лекарство и симптом — сразу после "
    "первой записи.\n"
    "Дальше запись делается одной кнопкой, без фото и подтверждения."
)

#: подписи разделов берём из клавиатуры — один список на всё приложение.
KIND_TITLES: dict[str, str] = dict(KIND_TABS)


def _rows(entries: list) -> list[tuple[int, str, str]]:
    return [(entry.id, entry.kind, entry.label) for entry in entries]


@router.message(F.text == "⭐️ Мой словарь")
@router.message(Command("my"))
async def show_dictionary(message: Message) -> None:
    await mark_used(message.chat.id, "dictionary")
    await _render(message, kind="meal", mode="use", offset=0)


async def _render(message: Message, *, kind: str, mode: str, offset: int, edit: bool = False) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        entries = await repo.list_dictionary(
            session, user, kind=kind, limit=PAGE_SIZE, offset=offset
        )
        rows = _rows(entries)
    if not rows and offset == 0:
        title = KIND_TITLES.get(kind, kind)
        text = f"{EMPTY_TEXT}\n\nРаздел «{title}» пока пуст."
    else:
        text = (
            "⭐️ <b>Личный словарь</b> · "
            + KIND_TITLES.get(kind, kind)
            + "\nСверху — то, что вы записывали последним.\n\n"
            + (
                "Нажмите, чтобы записать это ещё раз."
                if mode == "use"
                else "Нажмите, чтобы убрать запись."
            )
        )
    markup = dictionary_page(rows, kind=kind, mode=mode, offset=offset)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("dict:page:"))
async def on_page(callback: CallbackQuery) -> None:
    _, _, kind, raw_offset = callback.data.split(":", 3)
    await callback.answer()
    await _render(callback.message, kind=kind, mode="use", offset=int(raw_offset), edit=True)


@router.callback_query(F.data.startswith("dict:mode:"))
async def on_mode(callback: CallbackQuery) -> None:
    _, _, kind, mode = callback.data.split(":", 3)
    await callback.answer()
    await _render(callback.message, kind=kind, mode=mode, offset=0, edit=True)


@router.callback_query(F.data == "dict:close")
async def on_close(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("Словарь закрыт. Открыть снова — /my")


@router.callback_query(F.data.startswith("dict:rm:"))
async def on_remove(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.rsplit(":", 1)[1])
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        entry = await repo.get_dictionary_entry(session, user, entry_id)
        if entry is None:
            await callback.answer("Записи уже нет")
            return
        kind, label = entry.kind, entry.label
        await repo.hide_dictionary(session, entry)
    await callback.answer(f"Убрано: {label}")
    await _render(callback.message, kind=kind, mode="del", offset=0, edit=True)


@router.callback_query(F.data == "dict:new")
async def on_new(callback: CallbackQuery, state: FSMContext) -> None:
    """«Разобрать как новое» — fall through to the usual recognition path."""
    data = await state.get_data()
    text = data.get("dict_pending") or ""
    await callback.answer()
    await callback.message.edit_text("Хорошо, разбираю как новую запись…")
    await state.update_data({"dict_pending": None})
    if not text:
        return
    from src.handlers import views
    from src.vision import recognize

    try:
        draft = await recognize.parse_meal_text(text)
    except recognize.RecognitionError:
        await callback.message.answer(
            "Не понял, что записать. Опишите еду («овсянка с бананом») или пришлите фото.",
            reply_markup=await menu_of(callback.from_user.id),
        )
        return
    await views.show_meal_draft(callback.message, state, draft)


@router.callback_query(F.data.startswith("dict:use:"))
async def on_use(callback: CallbackQuery, state: FSMContext) -> None:
    """One tap, per kind.

    Еда и её составляющие — карточка подтверждения из `payload` (модель не
    зовётся); упаковка — та же карточка продукта; лекарство — приём «сейчас»;
    самочувствие — опрос с уже отмеченным симптомом.
    """
    entry_id = int(callback.data.rsplit(":", 1)[1])
    symptom_id: int | None = None
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        entry = await repo.get_dictionary_entry(session, user, entry_id)
        if entry is None or not entry.is_active:
            await callback.answer("Записи больше нет")
            return
        kind, label, payload = entry.kind, entry.label, dict(entry.payload or {})
        await repo.touch_dictionary(session, entry)
        if kind == "symptom":
            symptom_id = (await repo.upsert_symptom(session, user, label)).id
        if kind == "medication":
            taken_local = local_now(user)
            row = await repo.save_medication(
                session,
                user,
                taken_at=to_utc(taken_local, user),
                name=label,
                dose_text=payload.get("dose_text"),
                form=payload.get("form"),
                source="dictionary",
            )
            name = row.name
    await state.update_data({"dict_pending": None})
    if kind == "medication":
        await callback.answer("Записано")
        await callback.message.answer(
            f"✅ Записан приём: <b>{name}</b> в {taken_local:%H:%M}.", reply_markup=await menu_of(callback.from_user.id)
        )
        return

    await callback.answer()
    from src.handlers import views

    if kind == "symptom":
        # Симптом без оценки не запись: спрашиваем 1–5 и сохраняем как обычный
        # чек-ин (`spec/wellbeing.md`), симптом уже отмечен.
        from src.handlers.states import WellbeingFlow
        from src.handlers.wellbeing import ASK_SCORE, EXTRA_KEY, NOTE_KEY, SELECTED_KEY
        from src.keyboards import wellbeing_score

        await state.set_state(WellbeingFlow.scoring)
        await state.update_data(
            {
                SELECTED_KEY: [symptom_id] if symptom_id else [],
                EXTRA_KEY: [] if symptom_id else [label],
                NOTE_KEY: None,
            }
        )
        await callback.message.answer(
            f"🙂 <b>{label}</b>\n{ASK_SCORE}", reply_markup=wellbeing_score()
        )
        return

    if kind == "product":
        from src.vision.schemas import product_from_dict

        if not payload:
            await callback.message.answer(
                f"«{label}» — состав не сохранён, пришлите фото этикетки ещё раз."
            )
            return
        await views.show_product_draft(
            callback.message, state, product_from_dict(payload), mode="eaten"
        )
        return

    draft = meal_from_dict(payload) if payload.get("items") else MealDraft(title=label)
    draft.source = "dictionary"
    if not draft.items:
        await callback.message.answer(
            f"«{label}» — состав не сохранён, опишите порцию: <code>{label} 200</code>"
        )
        return
    await views.show_meal_draft(callback.message, state, draft)


async def offer_suggestions(message: Message, state: FSMContext, text: str) -> bool:
    """Show dictionary matches for `text`. True → handled, do not call the model."""
    if len(text.strip()) < MIN_PREFIX:
        return False
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        entries = await repo.suggest_dictionary(session, user, text, limit=SUGGEST_LIMIT)
        rows = _rows(entries)
    if not rows:
        return False
    await state.update_data({"dict_pending": text})
    await message.answer(
        f"Похоже на то, что вы уже записывали — «{text[:60]}».\n"
        "Нажмите нужное или разберу как новое:",
        reply_markup=dictionary_suggestions(rows),
    )
    return True


__all__ = [
    "MIN_PREFIX",
    "PAGE_SIZE",
    "SUGGEST_LIMIT",
    "offer_suggestions",
    "router",
    "show_dictionary",
]
