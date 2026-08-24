# Runbook — установка, запуск, эксплуатация

Техническая часть. Продуктовое описание — в [`README.md`](../README.md)
(ru) и [`README.en.md`](../README.en.md) (en).

## Быстрый старт (локально, без API-ключей)

```bash
git clone https://github.com/dansury/CGM-diet.git
cd CGM-diet
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# минимум: TELEGRAM_BOT_TOKEN от @BotFather, LLM_MOCK=true

DATABASE_URL="sqlite+aiosqlite:///data/cgm.db" python -m alembic upgrade head
python -m src.bot
```

`LLM_MOCK=true` поднимает бота целиком без ключей и без сети: распознавание
возвращает детерминированные заготовки, а вся статистика, графики и команды
работают по-настоящему. Это же режим для CI.

Демо-данные за 14 дней (чтобы сразу увидеть `/stats` и `/graph`):

```bash
DATABASE_URL="sqlite+aiosqlite:///data/cgm.db" python -m seeds.seed_demo <ваш_tg_id>
```

## Запуск с моделями

Получите ключ на [openrouter.ai](https://openrouter.ai/keys) и в `.env`:

```dotenv
OPENROUTER_API_KEY=sk-or-...
VISION_MODEL=google/gemini-2.5-flash
TEXT_MODEL=google/gemini-2.5-flash
LLM_MOCK=false
```

Голосовые сообщения — опционально, через любой OpenAI-совместимый
`/audio/transcriptions`:

```dotenv
STT_BASE_URL=https://api.openai.com/v1
STT_API_KEY=sk-...
STT_MODEL=whisper-1
```

Без этих переменных бот попросит написать текстом — остальное работает.

Владелец (`OWNER_TG_IDS`) может сменить модель прямо в переписке, не трогая
`.env`: `/model` — меню (все слоты / один слот / свободные модели), `/models` —
что сейчас стоит и на каком уровне. Выбор хранится в БД и переживает рестарт;
`.env` остаётся нижним уровнем. Подробности — [`spec/models.md`](../spec/models.md).

## Справочник побочных эффектов

Работает из коробки на committed-выборке (75 препаратов). Полный датасет —
ChSe-Decagon_monopharmacy (SNAP BioSNAP, Stanford), ~10 МБ:

```bash
python -m scripts.fetch_side_effects        # → data/side_effects/*.csv.gz
```

Соответствие «торговое название → действующее вещество → PubChem CID» лежит в
[`config/drug_cids.json`](../config/drug_cids.json) и пополняется вручную:
неверный CID показал бы чужие побочки, поэтому автоподбора здесь нет.

## Отчёты об ошибках

```dotenv
ERROR_REPORTS_ENABLED=true
ERROR_REPORT_TG_IDS=            # пусто -> OWNER_TG_IDS
```

Любая необработанная ошибка приходит владельцу одним сообщением: заголовок и
единый copy-paste блок с трейсбеком, контекстом апдейта и пользователем.
Пользователь видит только «что-то пошло не так, уже чиним». Повторы схлопываются
(5 минут), поток ограничен 8 сообщениями в минуту, сетевой шум не шлётся вовсе.
`/errors` показывает последние отчёты. Подробности — [`spec/errors.md`](../spec/errors.md).

## Docker

```bash
cp .env.example .env    # заполните TELEGRAM_BOT_TOKEN и OPENROUTER_API_KEY
docker compose up -d --build
docker compose logs -f bot
```

Поднимается PostgreSQL, прогоняются миграции, стартует бот. Данные — в томах
`pg_data` и `app_data`.

## Polling или webhook

По умолчанию — long polling, ничего настраивать не нужно.

Для вебхука (нужен HTTPS-домен):

```dotenv
BOT_MODE=webhook
WEBHOOK_BASE_URL=https://bot.example.com
WEBHOOK_SECRET=любая_длинная_строка
WEB_PORT=8080
```

Тот же процесс отдаёт `GET /health` (проба БД и конфигурации) и принимает
активность на `POST /health/samsung`.

---

## Health sync (Samsung Health → бот)

У Samsung Health **нет** серверного API для сторонних сервисов: данные покидают
телефон только через Health Connect. Поэтому интеграция сделана релеем —
приложение-мост на телефоне читает Health Connect и шлёт батчи боту.

1. Задайте на сервере `HEALTH_SYNC_SECRET=<длинная случайная строка>`.
2. Отправьте боту `/health` — он покажет ваш `chat_id` и персональный токен.
3. Настройте на телефоне отправку:

```http
POST https://bot.example.com/health/samsung
Content-Type: application/json
X-Health-Token: <токен из /health>

{
  "tg_id": 111222333,
  "source": "health_connect",
  "samples": [
    {"kind": "steps", "start": "2026-08-24T08:00:00Z",
     "end": "2026-08-24T08:15:00Z", "steps": 420, "external_id": "hc-1"},
    {"kind": "workout", "start": "2026-08-24T18:00:00Z",
     "end": "2026-08-24T18:40:00Z", "kcal": 210, "distance_m": 3200,
     "avg_hr": 118, "external_id": "hc-2"}
  ]
}
```

Ответ: `{"accepted": 2, "received": 2}`. Повторная отправка тех же
`external_id` даёт `accepted: 0` — релей идемпотентен, можно слать с запасом.
`kind`: `steps`, `workout`, `sleep`, `heart_rate`. Токен привязан к одному
пользователю; смена `HEALTH_SYNC_SECRET` отзывает все токены. Тот же эндпоинт
рассчитан и на Apple HealthKit.

---

## Как считается статистика

**Экскурсия.** Для каждого приёма пищи берётся базовая линия (среднее за 20 мин
до еды), затем пик внутри окна и разница между ними. Окон два и они
настраиваются: **45–90 мин** («через час») и **90–150 мин** («через два часа»).
Широкое окно — потому что CGM даёт точку раз в 5–15 минут, а люди не измеряют
ровно на 60-й минуте. Дополнительно считается iAUC — площадь над базовой линией.

Если внутри окна начался ещё один приём пищи, экскурсия помечается испорченной
и в статистику не идёт.

**Компоненты.** Выводы строятся не по блюдам, а по компонентам из закрытого
словаря (`добавленный сахар`, `белая мука`, `белый рис`, `цельное зерно`,
`клетчатка`, …). «Гречка с курицей» встретится 3 раза в месяц, «добавленный
сахар» — 40: выборка набирается кратно быстрее. Статистика по блюдам тоже есть,
переключателем.

**Достоверность.** Для каждого компонента: число наблюдений, среднее, медиана,
максимум, 95 % доверительный интервал и сравнение с приёмами пищи **без** этого
компонента (Манна–Уитни). Отсюда три уровня:

| Уровень | Условие |
|---|---|
| ⚪️ низкий | меньше 3 наблюдений или слабый сигнал — вывод не показывается |
| 🟡 средний | ≥ 5 наблюдений и (интервал выше нуля или p < 0.05) |
| 🟢 высокий | ≥ 8 наблюдений, интервал выше нуля, p < 0.05 и подъём ≥ 1.5 ммоль/л |

Рекомендации «сократить» выдаются только по компонентам с уровнем ≥ среднего.

**Формулировки.** Бот пишет «наблюдается связь» и «средний подъём», но никогда
«продукт повышает сахар»: это наблюдательные данные, а не эксперимент. На это
есть отдельный тест.

**Метрики глюкозы:** TIR/TAR/TBR (цель 3.9–10.0 ммоль/л), CV, GMI, расчётный
HbA1c, LBGI/HBGI, MAGE, J-index. Доли времени взвешены по времени, а не по числу
точек — иначе пачка ручных замеров перевесила бы спокойные сутки.

---

## Разработка

```bash
python -m pytest -q                     # 202 теста, ~15 с, без сети
python -m ruff check src tests seeds
DATABASE_URL="sqlite+aiosqlite:///data/build.db" \
  python -m alembic revision --autogenerate -m "описание"
```

### Структура

```
src/
  config.py logging_setup.py paths.py reporting.py export.py bot.py keyboards.py
  db/        base.py engine.py models.py repo.py
  llm/       base.py openrouter.py mock.py jsonx.py
  vision/    prompts.py recognize.py schemas.py
  ingest/    text_parse.py units.py pdf.py correction.py
  analytics/ tags.py windows.py stats.py cgm_metrics.py symptoms.py activity.py meds.py
  charts/    render.py
  meds/      catalog.py side_effects.py
  handlers/  common.py intake.py confirm.py wellbeing.py reports.py views.py deps.py
             states.py dictionary.py meds.py admin.py errors.py
  health/    samsung.py
  web/       app.py
  errors_report.py
config/   drug_cids.json side_effects_ru.json models.json
alembic/  seeds/  tests/  spec/  specs/  docs/  .specify/
```

### Документация

| Что | Где |
|---|---|
| Навигация по модулям | [`spec.md`](../spec.md) → `spec/<module>.md` |
| Принципы проекта | [`.specify/memory/constitution.md`](../.specify/memory/constitution.md) |
| Спецификация (Spec Kit) | [`specs/001-cgm-food-bot/`](../specs/001-cgm-food-bot/) |
| BMAD: бриф, PRD, архитектура, UX, эпики, QA | [`docs/bmad/`](bmad/) |
| Лекарства, словарь, ошибки, модели | [`spec/meds.md`](../spec/meds.md), [`spec/dictionary.md`](../spec/dictionary.md), [`spec/errors.md`](../spec/errors.md), [`spec/models.md`](../spec/models.md) |
| Разбор референсных репозиториев | [`docs/analysis/reference-repos.md`](analysis/reference-repos.md) |
| Проверка идеи на противоречия и UX | [`docs/analysis/contradictions-ux.md`](analysis/contradictions-ux.md) |
| План работ | [`TODO.md`](../TODO.md), [`DEV_PLAN.md`](../DEV_PLAN.md), [`DONE.md`](../DONE.md) |

---

## Переменные окружения

Полный список с комментариями — в [`.env.example`](../.env.example). Ключевые:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | обязательна |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/cgm.db` | `postgres://` и `sqlite://` нормализуются автоматически |
| `OPENROUTER_API_KEY` | — | без неё включается mock-режим |
| `LLM_MOCK` | `false` | `true` — полностью офлайн |
| `WINDOW_1H` / `WINDOW_2H` | `45-90` / `90-150` | окна в минутах |
| `BASELINE_WINDOW` | `20` | минут до еды для базовой линии |
| `MIN_OBSERVATIONS` | `3` | минимум наблюдений для показа компонента |
| `BOT_MODE` | `polling` | `polling` или `webhook` |
| `HEALTH_SYNC_SECRET` | — | нужен для релея Samsung Health |
| `OWNER_TG_IDS` | — | кому доступны `/model`, `/models`, `/errors` |
| `ERROR_REPORTS_ENABLED` | `true` | отчёты об ошибках владельцу |
| `ERROR_REPORT_TG_IDS` | — | кому слать отчёты; пусто — `OWNER_TG_IDS` |
| `FREE_FALLBACK_ENABLED` | `true` | повтор на свободных моделях при 429 |

## Лицензия и происхождение

Код написан с нуля. Идеи и клинические формулы сверялись с открытыми проектами
(`cgmquantify` — MIT, `Glucose360` — GPL-2.0, `FoodShot` — BUSL-1.1 и другие);
ни одна строка кода оттуда не заимствована. Подробный разбор с лицензиями —
в [`docs/analysis/reference-repos.md`](analysis/reference-repos.md).
Инфраструктурные паттерны (конфиг, движок БД, клиент OpenRouter, Docker)
переиспользованы из соседнего проекта того же владельца — GrowthProducer.
