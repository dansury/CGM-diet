# Implementation Plan: Дневник «еда → сахар → самочувствие»

**Branch**: `001-cgm-food-bot` · **Date**: 2026-08-24 · **Spec**: `./spec.md`

## Summary

Телеграм-бот на aiogram 3 принимает любой ввод, приводит его к четырём сущностям
(приём пищи, показание глюкозы, продукт, отметка самочувствия), связывает еду с
глюкозой по временным окнам и считает по компонентам блюд статистику подъёма
сахара с явным уровнем достоверности. Распознавание — OpenRouter (vision + chat)
с детерминированным mock-режимом; вся арифметика — обычный код с тестами.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: aiogram 3, SQLAlchemy 2 (async), Alembic, httpx, FastAPI, matplotlib
**Storage**: SQLite (личный инстанс) / PostgreSQL + asyncpg (прод)
**Testing**: pytest + pytest-asyncio, 157 тестов, mock-провайдер LLM
**Target Platform**: Linux-контейнер; long polling или webhook
**Project Type**: telegram-bot + небольшой HTTP-сервис
**Performance Goals**: ответ на фото ≤ 15 с; `/stats` за 30 дней ≤ 1 с на SQLite
**Constraints**: без SciPy/pandas; работоспособность без внешних API (mock)
**Scale/Scope**: единицы–сотни пользователей, ~5 записей в день на человека

## Constitution Check

| Принцип | Как соблюдён |
|---|---|
| I. Клиническая безопасность | Все тексты — только через `src/reporting.py`; `medications` — журнал, не назначения; тест запрещает причинные формулировки |
| II. Ассоциация, не причинность | `grade_confidence` + пороги `MIN_OBSERVATIONS`/`MEANINGFUL_RISE`; вывод без данных не показывается |
| III. Данные пользователя | `/export` (ZIP CSV), `/delete` (полное удаление), таблица `corrections` |
| IV. Подтверждай | Черновики в FSM, запись только по нажатию; кнопка смены единиц |
| V. Без внешних API | `LLM_MOCK=true` + `MockClient`, весь CI на нём |
| VI. Детерминизм | `ingest/text_parse.py`, `ingest/units.py`, `analytics/*` — чистый код с тестами |

Отклонений нет.

## Architecture

```
Telegram ──▶ handlers/intake ──▶ vision/recognize ──▶ drafts (FSM)
                                        │                  │
                                        ▼                  ▼
                              llm/{openrouter,mock}   handlers/confirm ──▶ db/repo
                                                                              │
HTTP relay ─▶ web/app /health/samsung ─▶ health/samsung ─────────────────────▶┤
                                                                              ▼
                                              analytics/{windows,stats,cgm_metrics,
                                              symptoms,activity} ──▶ reporting ──▶ charts
```

**Слои и правила:**
1. `handlers/*` не считают и не форматируют — только маршрутизация и FSM.
2. `analytics/*` не знают про ORM: работают с `MealLike`/`GlucosePoint`/`CheckinLike`.
3. `db/repo.py` — единственная точка доступа к БД; там же граница timezone-aware.
4. `reporting.py` — единственное место, где рождаются пользовательские выводы.

## Key Design Decisions

**Окна вместо точечных измерений.** CGM даёт точку каждые 5–15 минут, пальцевые
измерения — когда получится. Поэтому «через час» — это диапазон 45–90 мин, а
дельта = пик в окне минус базовая линия, а не разница двух конкретных точек.

**Компоненты, а не блюда, как основная единица вывода.** «Гречка с курицей»
встретится 3 раза за месяц, «добавленный сахар» — 40. Статистика по закрытому
словарю тегов (`analytics/tags.py`) набирает мощность на порядок быстрее.
Статистика по блюдам остаётся доступной второй вкладкой.

**Контраст, а не абсолютное значение.** Показывать «после сахара +3.2» без
«без сахара +0.9» бессмысленно: у человека с инсулинорезистентностью растёт
всё. Поэтому в каждом выводе есть группа сравнения и Mann–Whitney U.

**mmol/L как каноническая единица.** Клинические формулы (GMI, eA1c, LBGI)
определены в mg/dL и конвертируют внутри себя; хранение единообразное.

**Отсутствие серверного API у Samsung Health.** Интеграция сделана релеем:
приложение на телефоне читает Health Connect и шлёт батчи на `/health/samsung`
с per-user HMAC-токеном. Та же схема без изменений подходит для Apple HealthKit.

## Project Structure

```
src/
  config.py logging_setup.py paths.py reporting.py export.py bot.py
  db/       base.py engine.py models.py repo.py
  llm/      base.py openrouter.py mock.py jsonx.py
  vision/   prompts.py recognize.py schemas.py
  ingest/   text_parse.py units.py pdf.py
  analytics/ tags.py windows.py stats.py cgm_metrics.py symptoms.py activity.py
  charts/   render.py
  handlers/ common.py intake.py confirm.py wellbeing.py reports.py views.py deps.py states.py
  health/   samsung.py
  web/      app.py
alembic/  seeds/  tests/  spec/  docs/
```

## Complexity Tracking

Отклонений от конституции нет; таблица не заполняется.
