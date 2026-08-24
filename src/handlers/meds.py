"""Medications: photo → card → journal, plus the reference lookup.

The bot logs; it does not prescribe. Every text the user sees here is built in
`src/reporting.py` (`spec/meds.md`, constitution I).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.analytics import meds as meds_analytics
from src.db import repo
from src.handlers.deps import local_now, session_scope, to_local, to_utc
from src.handlers.states import MedicationFlow
from src.handlers.views import DRAFT_KEY, FILES_KEY, TAKEN_AT_KEY, show_medication_draft
from src.logging_setup import get_logger
from src.meds.side_effects import match_symptoms
from src.reporting import format_med_side_effects, format_medications
from src.vision.schemas import MedicationDraft, med_from_dict

router = Router(name="meds")
log = get_logger("handlers.meds")

JOURNAL_DAYS = 30


@router.message(F.text == "💊 Лекарства")
@router.message(Command("meds"))
async def show_journal(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        since = local_now(user) - timedelta(days=JOURNAL_DAYS)
        rows = await repo.load_medications(session, user, since=to_utc(since, user))
        journal = [(to_local(row.taken_at, user), row.name, row.dose_text) for row in rows]
        checkins = await repo.load_checkin_likes(session, user, since=to_utc(since, user))
        med_likes = await repo.load_medication_likes(session, user, since=to_utc(since, user))

    await message.answer(format_medications(journal, days=JOURNAL_DAYS))

    labels = sorted({label for checkin in checkins for label in checkin.symptoms})
    if not labels or not med_likes:
        return
    links = meds_analytics.symptom_links(
        med_likes, checkins, lambda name: match_symptoms(name, labels)
    )
    text = format_med_side_effects(links)
    if text:
        await message.answer(text)


# ------------------------------------------------------------------ confirm

@router.callback_query(F.data == "med:ok", MedicationFlow.confirming)
async def med_ok(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = med_from_dict(data.get(DRAFT_KEY) or {})
    raw_stamp = data.get(TAKEN_AT_KEY)
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        taken_local = datetime.fromisoformat(raw_stamp) if raw_stamp else local_now(user)
        media_id = None
        file_ids = data.get(FILES_KEY) or []
        if file_ids:
            media = await repo.save_media(
                session, user, kind="medication", tg_file_id=file_ids[0]
            )
            media_id = media.id
        row = await repo.save_medication_draft(
            session,
            user,
            draft,
            taken_at=to_utc(taken_local, user),
            media_id=media_id,
            source="photo" if file_ids else "text",
        )
        known = bool(row.cid)
    await state.clear()
    await callback.answer("Записано")
    tail = (
        "Препарат есть в справочнике — если отметите самочувствие, покажу совпадения "
        "с известными побочными эффектами."
        if known
        else "Препарата нет в справочнике побочных эффектов — записал как есть."
    )
    await callback.message.edit_text(
        f"✅ Записан приём: <b>{row.name}</b> в {taken_local:%H:%M}.\n"
        f"Он теперь в личном словаре — в следующий раз хватит одной кнопки (/my).\n{tail}"
    )


@router.callback_query(F.data == "med:edit", MedicationFlow.confirming)
async def med_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MedicationFlow.editing)
    await callback.answer()
    await callback.message.answer(
        "Напишите или наговорите, что поправить — например:\n"
        "<code>метформин 1000</code> · <code>это конкор</code> · <code>дозировка 5 мг</code>"
    )


@router.message(MedicationFlow.editing)
async def med_apply_edit(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    instruction = (text_override or message.text or "").strip()
    if not instruction:
        await message.answer("Не расслышал. Напишите название и дозировку.")
        return
    data = await state.get_data()
    old = med_from_dict(data.get(DRAFT_KEY) or {})
    new, applied = _apply_med_correction(old, instruction)
    if not applied:
        await message.answer(
            "Не понял правку. Напишите название препарата и дозировку — "
            "например <code>метформин 1000 мг</code>."
        )
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.save_correction(
            session,
            user,
            entity_type="medication_draft",
            entity_id=None,
            field="name",
            old_value=f"{old.name} {old.dose_text or ''}".strip(),
            new_value=instruction,
        )
    raw_stamp = data.get(TAKEN_AT_KEY)
    await show_medication_draft(
        message,
        state,
        new,
        file_ids=data.get(FILES_KEY),
        taken_at_local=datetime.fromisoformat(raw_stamp) if raw_stamp else None,
        applied=applied,
    )


def _apply_med_correction(
    draft: MedicationDraft, instruction: str
) -> tuple[MedicationDraft, list[str]]:
    """Merge, do not replace: a corrected dose keeps the recognised name.

    The dose part of the instruction («850 мг») updates the dose; the rest, if
    any, becomes the name. Anything the user did not mention survives.
    """
    import re

    from src.meds.catalog import normalize_drug

    applied: list[str] = []
    new = MedicationDraft(
        name=draft.name,
        inn=draft.inn,
        dose_text=draft.dose_text,
        form=draft.form,
        note=draft.note,
        confidence=1.0,
        raw_text=instruction,
    )
    text = instruction.strip()
    dose_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(мг|мкг|г|мл|ме|iu|таб\w*)", text, re.I)
    if dose_match:
        dose = dose_match.group(0).strip()
        if dose != (draft.dose_text or ""):
            applied.append(f"дозировка — {draft.dose_text or '—'} → {dose}")
        new.dose_text = dose
        text = (text[: dose_match.start()] + " " + text[dose_match.end() :]).strip()
    name = re.sub(
        r"^(?:это|препарат|лекарство|называется|не\s+\S+\s*,?\s*а)\s+", "", text, flags=re.I
    ).strip(" .,")
    if name and normalize_drug(name) != normalize_drug(draft.name):
        applied.append(f"название — {draft.name or '—'} → {name}")
        new.name = name
        new.inn = None  # the substance no longer follows from the old package
    return new, applied


@router.callback_query(F.data == "med:time", MedicationFlow.confirming)
async def med_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MedicationFlow.retiming)
    await callback.answer()
    await callback.message.answer(
        "Во сколько был приём? Например <code>08:30</code> или «вчера вечером»."
    )


@router.message(MedicationFlow.retiming)
async def med_apply_time(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    from src.ingest.text_parse import parse_text

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
    parsed = parse_text(text_override or message.text or "", now=now)
    if parsed.at is None:
        await message.answer("Не понял время. Напишите, например, <code>08:30</code>.")
        return
    data = await state.get_data()
    await show_medication_draft(
        message,
        state,
        med_from_dict(data.get(DRAFT_KEY) or {}),
        file_ids=data.get(FILES_KEY),
        taken_at_local=parsed.at,
    )


@router.callback_query(F.data == "med:drop")
async def med_drop(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Приём не записан.")


__all__ = ["JOURNAL_DAYS", "router", "show_journal"]
