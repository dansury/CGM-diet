"""Анализы: журнал маркеров и продукты-источники (`spec/labs.md`).

Ввод анализов (фото, PDF, текст) живёт в `intake`/`confirm`; здесь — только
взгляд назад: что сохранено, что вне референса из самого документа и какими
продуктами богат соответствующий нутриент.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.analytics import labs as labs_math
from src.db import repo
from src.db.models import User
from src.handlers.deps import session_scope
from src.logging_setup import get_logger
from src.reporting import format_lab_review

router = Router(name="labs")
log = get_logger("handlers.labs")


async def lab_review_text(
    session: AsyncSession, user: User, *, header: str = "🧪 <b>Ваши анализы</b>"
) -> str:
    values = await repo.load_lab_values(session, user)
    return format_lab_review(labs_math.review(values), header=header)


@router.message(Command("labs"))
async def cmd_labs(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.from_user.id)
        text = await lab_review_text(session, user)
        await repo.mark_feature_used(session, user, "labs")
    await message.answer(text)


__all__ = ["lab_review_text", "router"]
