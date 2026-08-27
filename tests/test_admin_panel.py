"""Панель владельца: реестр пользователей и разделы `/bot_settings`.

`spec/bot.md` § Панель владельца. Проверяем то, что читает базу и складывает
текст; фильтр «владелец + личка» — обвязка aiogram, её не тестируем.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db import repo
from src.handlers import admin_panel
from src.vision.schemas import ItemDraft, MealDraft

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 25, 13, 0, tzinfo=UTC)


async def test_the_roster_shows_a_user_and_what_they_recorded(engine, session):
    user = await repo.get_or_create_user(session, 777, username="tester", first_name="Тест")
    await repo.save_meal(
        session,
        user,
        MealDraft(title="Гречка", items=[ItemDraft(name="гречка", portion_g=200)]),
        eaten_at=NOW,
    )
    await session.commit()

    text = await admin_panel.render_users()
    assert "Пользователи (1)" in text
    assert "<code>777</code>" in text
    assert "@tester" in text
    assert "🍽 1" in text


async def test_the_roster_says_so_when_nobody_has_started_yet(engine, session):
    assert "Пока никого" in await admin_panel.render_users()


async def test_the_data_section_counts_the_whole_base(engine, session):
    user = await repo.get_or_create_user(session, 778)
    await repo.save_meal(
        session, user, MealDraft(title="Обед", items=[ItemDraft(name="обед")]), eaten_at=NOW
    )
    await session.commit()

    text = await admin_panel.render_data()
    assert "Приёмы пищи: <b>1</b>" in text
    assert "Пользователи: <b>1</b>" in text


async def test_the_panel_offers_every_section_it_can_render():
    text, markup = admin_panel.owner_panel()
    assert "Панель владельца" in text
    sections = {
        button.callback_data.removeprefix(admin_panel.CB_PREFIX)
        for row in markup.inline_keyboard
        for button in row
    }
    assert sections == set(admin_panel._RENDERERS)


async def test_a_clean_run_reports_no_errors():
    assert "Чисто" in admin_panel.render_errors()


async def test_health_answers_about_the_database_and_the_model(engine, session):
    text = await admin_panel.render_health()
    assert "БД отвечает" in text
    assert "LLM" in text
