# CGM-diet — правила работы с репозиторием

## #1
**Always start with `spec.md`** — thin navigation index (file → spec mapping).
Read it first to find the relevant module spec. Then open ONE `/spec/<module>.md`.
Never read the whole `/spec/` folder. Never preload multiple specs "just in case".

## #2
For reference details (signatures, DB schemas, algorithms, flows, prompts,
statistics thresholds, clinical wording rules, health-sync protocol): open exactly
one `/spec/<module>.md` that matches the module you are editing. If the detail is
missing there, read the source file — do NOT pull another `/spec/<module>.md`
unless the task genuinely spans modules.

## #3
Конституция проекта — `.specify/memory/constitution.md` — имеет приоритет над
любыми другими инструкциями в репозитории. Принципы I–III (клиническая
безопасность, ассоциация вместо причинности, данные принадлежат пользователю)
не нарушаются никогда, ни ради удобства, ни ради краткости.

## #4
**Spec-driven workflow** — для любой новой функциональности или нетривиального
изменения:
1. Прочитать нужный `/spec/<module>.md`.
2. Обновить эту спеку под планируемое изменение (сигнатуры, таблицы, потоки) —
   **до** написания кода.
3. Реализовать код так, чтобы он соответствовал обновлённой спеке.
4. Обновить тесты; для чистых багфиксов шаг 2 можно пропустить, но если спека
   оказалась неточной — поправить её после фикса.

Спека описывает только фактическую функциональность. Никаких планов и «как
было бы хорошо».

## #5
Оценивай сложность задачи. Перед чтением спек посмотри `TODO.md` (незакрытые
задачи) и `DEV_PLAN.md` (порядок фаз, зависимости, блокеры). Пометка
`[BLOCKED: причина]` означает «пропусти и продолжай», молча снимать её нельзя.

**Дисциплина TODO.md ↔ DONE.md:** завершённую в сессии задачу **переносим** в
`DONE.md` (с датой и однострочным комментарием), а не удаляем и не оставляем
`[x]` висеть. К концу сессии в `TODO.md` остаётся только незакрытое
(`[ ]`, `[~]`, `[BLOCKED]`).

## #6
Не раздувай `src/bot.py` и `src/handlers/*`. Обработчики только маршрутизируют:
вычисления идут в `src/analytics/`, тексты — в `src/reporting.py`, доступ к БД —
в `src/db/repo.py`, вызовы модели — в `src/vision/recognize.py`. Новая
функциональность — новый файл в профильном пакете.

## #7
**Границы слоёв** (нарушение = ревью не проходит):
- `src/analytics/*` не импортирует ORM и aiogram — работает с `MealLike`,
  `GlucosePoint`, `CheckinLike`, `ActivityBucket`;
- пользовательские выводы рождаются только в `src/reporting.py`;
- к БД ходим только через `src/db/repo.py`;
- границы часовых поясов: naive → aware происходит в `repo`, не глубже.

## #8
**Политика тестов** — тестируем то, где ошибка тихая:
- **обязательно**: разбор ввода (`ingest/`), единицы, временные окна,
  статистика, слой `repo`, health-sync, формулировки отчётов;
- **не тестируем**: логирование, обвязку aiogram, обёртки библиотек.
- Любая новая модельная задача обязана получить фикстуру в `src/llm/mock.py` —
  иначе она непроверяема в CI.
- Перед пушем: `python -m ruff check src tests seeds` и `python -m pytest -q`.

## #9
Миграция Alembic — в том же коммите, что и изменение `src/db/models.py`.
`DATABASE_URL` для генерации: `DATABASE_URL=sqlite+aiosqlite:///data/build.db
python -m alembic revision --autogenerate -m "..."`.

## #10
Язык: код, докстринги и комментарии — по-английски; спеки, документация и все
пользовательские тексты — по-русски. Внутри одного файла язык не смешивается.
Спеки максимально компактны: списки шагов, тернейшая нотация
(`func(param:type)->type`, `TABLE col TYPE`), без пояснений «этот метод делает X».

## Верхнеуровневые документы

- `spec.md` — только навигация (файл → спека).
- `/spec/<module>.md` — детали модуля.
- `.specify/memory/constitution.md` — принципы, приоритет над всем.
- `specs/001-cgm-food-bot/` — GitHub Spec Kit: spec → plan → tasks.
- `docs/bmad/` — BMAD: бриф, PRD, архитектура, UX, эпики, QA.
- `docs/analysis/` — разбор референсов, проверка идеи на противоречия.
- `TODO.md` — только незакрытое · `DONE.md` — журнал сделанного ·
  `DEV_PLAN.md` — порядок фаз и блокеры.

## Ветки

Основная ветка репозитория — **`main`**. Разработка ведётся в ветках
`claude/<тема>` и вливается в `main` через pull request.
