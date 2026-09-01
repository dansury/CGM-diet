"""Owner-only commands: model selection and the error log.

Private chat only, `OWNER_TG_IDS` only. A non-owner is not refused — the router
simply does not match, so the update falls through to the normal handlers and
the bot never leaks that these commands exist. See `spec/models.md`.
"""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import load_settings
from src.db import repo
from src.errors_report import recent_reports, render_report
from src.handlers.deps import session_scope
from src.llm import model_selection, set_free_alternates
from src.logging_setup import get_logger

router = Router(name="admin")
log = get_logger("handlers.admin")

CANDIDATES_KEY = "mdl_candidates"
TARGET_KEY = "mdl_target"


def _is_owner(user_id: int | None) -> bool:
    return user_id is not None and load_settings().is_owner(user_id)


owner_filter = F.func(lambda event: _is_owner(getattr(event.from_user, "id", None)))
router.message.filter(F.chat.type == "private", owner_filter)
router.callback_query.filter(owner_filter)


# ------------------------------------------------------------------ state

async def load_active_models() -> dict[str, str]:
    """Fill the process cache from `settings_kv`. Called at startup and on change."""
    async with session_scope() as session:
        stored = {
            model_selection.KEY_GLOBAL: await repo.get_setting(
                session, model_selection.KEY_GLOBAL
            ),
            model_selection.KEY_SLOTS: await repo.get_setting(session, model_selection.KEY_SLOTS),
        }
    resolved = model_selection.resolve_all(stored)
    mapping = {slot: item.model_id for slot, item in resolved.items()}
    model_selection.refresh(mapping)
    return mapping


async def explain_models() -> dict[str, model_selection.Resolved]:
    async with session_scope() as session:
        stored = {
            model_selection.KEY_GLOBAL: await repo.get_setting(
                session, model_selection.KEY_GLOBAL
            ),
            model_selection.KEY_SLOTS: await repo.get_setting(session, model_selection.KEY_SLOTS),
        }
    return model_selection.resolve_all(stored)


LEVEL_NAMES = {"slot": "слот", "global": "общий", "env": "из .env"}


@router.message(Command("models"))
async def show_models(message: Message) -> None:
    resolved = await explain_models()
    lines = ["🤖 <b>Модели по слотам</b>", ""]
    for slot, item in resolved.items():
        lines.append(
            f"• <b>{slot}</b> — <code>{item.model_id}</code> "
            f"({LEVEL_NAMES.get(item.level, item.level)})\n"
            f"  <i>{model_selection.SLOT_LABELS.get(slot, '')}</i>"
        )
    lines.append("")
    lines.append("Сменить — /model")
    await message.answer("\n".join(lines))


@router.message(Command("model"))
async def choose_level(message: Message) -> None:
    await message.answer(
        "🤖 <b>Смена нейросети</b>\n\n"
        "«Все слоты» ставит одну модель на всё, но индивидуальные выборы "
        "сохраняются и остаются в приоритете.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Все слоты", callback_data="mdl:lvl:global")],
                [InlineKeyboardButton(text="🎯 Один слот", callback_data="mdl:lvl:slot")],
                [InlineKeyboardButton(text="🆓 Свободные модели", callback_data="mdl:lvl:free")],
            ]
        ),
    )


def _model_rows(models: list[model_selection.CatalogModel], target: str) -> InlineKeyboardMarkup:
    rows = []
    for index, model in enumerate(models):
        price = "🆓" if model.tier == "free" else f"${model.usd_per_1k:.4f}/1k"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{model.label} · {price}"[:64],
                    callback_data=f"mdl:set:{target}:{index}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="mdl:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _offer(message: Message, state: FSMContext, models: list, target: str, title: str) -> None:
    if not models:
        await message.edit_text("Каталог пуст — проверьте config/models.json.")
        return
    await state.update_data(
        {
            CANDIDATES_KEY: [
                {
                    "id": m.id,
                    "label": m.label,
                    "tier": m.tier,
                    "usd_per_1k": m.usd_per_1k,
                }
                for m in models
            ],
            TARGET_KEY: target,
        }
    )
    await message.edit_text(title, reply_markup=_model_rows(models, target))


@router.callback_query(F.data.startswith("mdl:lvl:"))
async def on_level(callback: CallbackQuery, state: FSMContext) -> None:
    level = callback.data.rsplit(":", 1)[1]
    await callback.answer()
    if level == "slot":
        await callback.message.edit_text(
            "Какой слот меняем?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{slot} — {model_selection.SLOT_LABELS.get(slot, '')}"[:64],
                            callback_data=f"mdl:slot:{slot}",
                        )
                    ]
                    for slot in model_selection.SLOTS
                ]
            ),
        )
        return
    if level == "free":
        from src.llm.free_catalog import load_free_models

        free = await load_free_models()
        set_free_alternates([m.id for m in free])
        models = [
            model_selection.CatalogModel(id=m.id, label=m.label, tier="free", usd_per_1k=0.0)
            for m in free[:12]
        ]
        await _offer(
            callback.message,
            state,
            models,
            "text",
            "🆓 <b>Свободные модели</b> (каталог shir-man)\n"
            "Выбранная встанет в слот текста; остальные пойдут в цепочку фолбэка на 429.",
        )
        return
    await _offer(
        callback.message,
        state,
        list(model_selection.candidates("text")),
        "global",
        "🌐 Модель на все слоты:",
    )


@router.callback_query(F.data.startswith("mdl:slot:"))
async def on_slot(callback: CallbackQuery, state: FSMContext) -> None:
    slot = callback.data.rsplit(":", 1)[1]
    await callback.answer()
    await _offer(
        callback.message,
        state,
        list(model_selection.candidates(slot)),
        slot,
        f"🎯 Модель для слота <b>{slot}</b>:",
    )


@router.callback_query(F.data.startswith("mdl:set:"))
async def on_set(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, target, raw_index = callback.data.split(":", 3)
    data = await state.get_data()
    candidates = data.get(CANDIDATES_KEY) or []
    try:
        chosen = candidates[int(raw_index)]
    except (ValueError, IndexError):
        await callback.answer("Список устарел, откройте /model заново", show_alert=True)
        return

    async with session_scope() as session:
        if target == "global":
            await repo.set_setting(session, model_selection.KEY_GLOBAL, chosen["id"])
        else:
            stored = dict(await repo.get_setting(session, model_selection.KEY_SLOTS) or {})
            stored[target] = chosen["id"]
            await repo.set_setting(session, model_selection.KEY_SLOTS, stored)
    mapping = await load_active_models()
    await state.update_data({CANDIDATES_KEY: None, TARGET_KEY: None})
    await callback.answer("Сменил")
    scope = "на все слоты" if target == "global" else f"для слота {target}"
    lines = [f"✅ <b>{chosen['label']}</b> {scope}.", ""]
    lines += [f"• {slot}: <code>{model}</code>" for slot, model in mapping.items()]
    await callback.message.edit_text("\n".join(lines))


@router.callback_query(F.data == "mdl:close")
async def on_close(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("Ок, ничего не меняю.")


# ------------------------------------------------------------------ errors

@router.message(Command("errors"))
async def show_errors(message: Message) -> None:
    reports = recent_reports(5)
    if not reports:
        await message.answer("✅ Ошибок в этом запуске не было.")
        return
    await message.answer(f"🧾 Последние ошибки: {len(reports)}")
    for report in reports:
        await message.answer(render_report(report))


@router.message(Command("whereami"))
async def show_context(message: Message) -> None:
    """Owner self-check: which chat id to put into OWNER_TG_IDS."""
    from src.config import load_settings
    from src.db.persistence import describe_storage
    from src.meds.side_effects import dataset_status

    status = dataset_status()
    storage = describe_storage(load_settings())
    durability = "переживёт перезапуск" if storage.durable else "⚠️ пропадёт при обновлении"
    await message.answer(
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"база: {storage.kind} — {durability}\n"
        f"<code>{html.escape(storage.location)}</code>\n"
        f"справочник побочек: {status.rows} записей по {status.drugs} препаратам"
        f"{' (выборка)' if status.sample else ''}\n"
        f"<code>{html.escape(status.path)}</code>"
    )


__all__ = ["explain_models", "load_active_models", "router"]
