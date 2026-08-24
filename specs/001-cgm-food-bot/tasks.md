# Tasks: Дневник «еда → сахар → самочувствие»

**Input**: `./spec.md`, `./plan.md` · **Статус**: MVP закрыт, см. `../../DONE.md`

Формат: `[ID] [P?] описание` · `[P]` — можно делать параллельно (разные файлы).

## Phase 1 — Фундамент (завершено)

- [X] T001 Конфиг из окружения, нормализация `DATABASE_URL`, окна из env — `src/config.py`
- [X] T002 [P] Структурное логирование — `src/logging_setup.py`
- [X] T003 [P] Пути ресурсов, не зависящие от CWD — `src/paths.py`
- [X] T004 Async-движок и sessionmaker — `src/db/engine.py`
- [X] T005 16 ORM-таблиц по модели данных — `src/db/models.py`
- [X] T006 Alembic env + начальная миграция — `alembic/`

## Phase 2 — Распознавание

- [X] T007 Типы и протокол LLM-клиента — `src/llm/base.py`
- [X] T008 OpenRouter: chat + vision + STT, ретраи — `src/llm/openrouter.py`
- [X] T009 Mock-провайдер и фикстуры по задачам — `src/llm/mock.py`
- [X] T010 [P] Толерантный разбор JSON из ответа модели — `src/llm/jsonx.py`
- [X] T011 Промпты пяти задач + классификатор фото — `src/vision/prompts.py`
- [X] T012 Валидация ответов в черновики — `src/vision/recognize.py`
- [X] T013 [P] Единицы глюкозы и правдоподобие — `src/ingest/units.py`
- [X] T014 Детерминированный разбор текста — `src/ingest/text_parse.py`
- [X] T015 [P] Извлечение текста из PDF — `src/ingest/pdf.py`

## Phase 3 — Аналитика

- [X] T016 Словарь компонентов и нормализация названий — `src/analytics/tags.py`
- [X] T017 Окна, baseline, пик, дельта, iAUC, контаминация — `src/analytics/windows.py`
- [X] T018 Агрегация, t-CI, Mann–Whitney U, уровни достоверности — `src/analytics/stats.py`
- [X] T019 [P] CGM-метрики (TIR, CV, GMI, eA1c, LBGI/HBGI, MAGE) — `src/analytics/cgm_metrics.py`
- [X] T020 [P] Симптомы против сахара — `src/analytics/symptoms.py`
- [X] T021 [P] Еда против активности — `src/analytics/activity.py`

## Phase 4 — Бот

- [X] T022 Репозиторий БД, глоссарий, удаление данных — `src/db/repo.py`
- [X] T023 Клавиатуры и грамматика callback-data — `src/keyboards.py`
- [X] T024 Онбординг, меню, настройки — `src/handlers/common.py`
- [X] T025 Интейк: фото/альбомы/документы/голос/текст — `src/handlers/intake.py`
- [X] T026 Подтверждение и правки черновиков — `src/handlers/confirm.py`
- [X] T027 Самочувствие и динамический глоссарий — `src/handlers/wellbeing.py`
- [X] T028 Отчёты и команды — `src/handlers/reports.py`
- [X] T029 [P] Формулировки выводов — `src/reporting.py`
- [X] T030 [P] Графики — `src/charts/render.py`
- [X] T031 [P] Экспорт CSV — `src/export.py`

## Phase 5 — Интеграции и упаковка

- [X] T032 Релей Samsung Health / Health Connect — `src/health/samsung.py`
- [X] T033 FastAPI: webhook, /health, приём активности — `src/web/app.py`
- [X] T034 [P] Dockerfile, docker-compose, entrypoint
- [X] T035 [P] Seed демо-данных на 14 дней — `seeds/seed_demo.py`
- [X] T036 Тесты (157) — `tests/`

## Phase 6 — Дальше (не в MVP)

- [ ] T037 Apple HealthKit по той же схеме релея
- [ ] T038 Прямой импорт CSV из LibreView / Dexcom Clarity
- [ ] T039 Штрихкод локально (pyzbar) + Open Food Facts как источник состава
- [ ] T040 Напоминания «померить через час» после записи еды
- [ ] T041 Кэш `food_stats` вместо пересчёта на каждый `/stats`
- [ ] T042 Недельный дайджест владельцу дневника
