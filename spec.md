# spec.md — навигационный индекс

Тонкий индекс: файл → спека. Деталей здесь нет. Правила чтения — в `CLAUDE.md`.

## Модули

| Спека | Покрывает | Исходники |
|---|---|---|
| [spec/infra.md](spec/infra.md) | конфиг, логирование, пути, движок БД, миграции, Docker | `src/config.py`, `src/logging_setup.py`, `src/paths.py`, `src/db/{base,engine}.py`, `alembic/`, `Dockerfile`, `docker-compose.yml`, `scripts/` |
| [spec/data_model.md](spec/data_model.md) | 17 таблиц, соглашения хранения, репозиторий, экспорт, удаление | `src/db/models.py`, `src/db/repo.py`, `src/export.py` |
| [spec/llm.md](spec/llm.md) | протокол клиента, OpenRouter, mock, разбор JSON | `src/llm/*` |
| [spec/ingest.md](spec/ingest.md) | маршрутизация ввода, промпты, распознавание, голос (SpeechKit), текст, единицы, PDF | `src/vision/*`, `src/ingest/*` |
| [spec/analytics.md](spec/analytics.md) | компоненты, окна, статистика, CGM-метрики, симптомы, активность | `src/analytics/*` |
| [spec/bot.md](spec/bot.md) | команды, клавиатуры, FSM, обработчики, отчёты | `src/bot.py`, `src/handlers/*`, `src/keyboards.py` |
| [spec/wellbeing.md](spec/wellbeing.md) | опрос 1–5, динамический глоссарий симптомов | `src/handlers/wellbeing.py`, `src/analytics/symptoms.py` |
| [spec/charts.md](spec/charts.md) | таймлайн, рейтинг, самочувствие | `src/charts/render.py` |
| [spec/health_sync.md](spec/health_sync.md) | Samsung Health / Health Connect, HTTP-релей, вебхук, инструкция и приложение-мост | `src/health/*`, `src/web/app.py`, `apps/health-bridge/` |
| [spec/meds.md](spec/meds.md) | лекарства: фото, журнал, справочник побочек | `src/meds/*`, `src/analytics/meds.py`, `src/handlers/meds.py` |
| [spec/dictionary.md](spec/dictionary.md) | личный словарь, подсказки по первым буквам, память БЖУ | `src/handlers/dictionary.py`, `src/db/repo.py` |
| [spec/errors.md](spec/errors.md) | отчёты об ошибках: админу подробно, пользователю коротко | `src/errors_report.py`, `src/handlers/errors.py`, `src/logging_setup.py` |
| [spec/models.md](spec/models.md) | выбор модели владельцем, свободные модели, фолбэк 429 | `src/llm/{model_selection,free_catalog,fallback}.py`, `src/handlers/admin.py` |
| [spec/clinical.md](spec/clinical.md) | что можно и чего нельзя говорить пользователю | `src/reporting.py` |

## Другие документы

- `docs/bmad/` — BMAD: бриф, PRD, архитектура, UX, эпики, QA.
- `specs/001-cgm-food-bot/` — GitHub Spec Kit: spec → plan → tasks.
- `.specify/memory/constitution.md` — конституция проекта (приоритет над всем).
- `docs/analysis/` — разбор референсных репозиториев, проверка идеи на противоречия.
- `TODO.md` / `DEV_PLAN.md` / `DONE.md` — план работ.
