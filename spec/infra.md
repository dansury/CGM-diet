# infra — конфиг, логирование, БД, упаковка

## Config & Environment (`src/config.py`)

`Settings` — frozen dataclass, читается из окружения через `load_settings()`
(кэш процесса, `refresh=True` для тестов).

```
telegram_bot_token:str  owner_tg_ids:tuple[int]  database_url:str
openrouter_api_key:str  openrouter_base_url:str  vision_model:str  text_model:str  llm_mock:bool
stt_base_url:str  stt_api_key:str  stt_model:str
app_env:str  bot_mode:polling|webhook  webhook_base_url:str  webhook_secret:str  web_host:str  web_port:int
default_glucose_unit:str  window_1h:(int,int)  window_2h:(int,int)  baseline_window:int  min_observations:int
health_sync_secret:str
```

- `normalize_database_url(url)->str` — `postgres://`, `postgresql://`,
  `postgres+asyncpg://` → `postgresql+asyncpg://`; `sqlite://` → `sqlite+aiosqlite://`.
  Хостинги отдают неасинхронный вариант, что роняет alembic и бота на старте.
- `ensure_sqlite_parent_dir(url)` — SQLite не создаёт родительскую директорию.
- `_read_window("45-90")->(45,90)`; `start >= end` → `ConfigError`.
- `vision_available` / `stt_available` — mock считается доступным провайдером.

## Logging (`src/logging_setup.py`)

`setup_logging(level, json_output)` идемпотентен. `LOG_JSON=true` → одна строка
JSON на запись (`ts, level, logger, msg, exc`). Шумные логгеры (`httpx`,
`aiogram.event`, `matplotlib`) прижаты к WARNING. `get_logger(name)` — единственная
точка входа.

## Resource paths (`src/paths.py`)

`APP_ROOT` = директория, содержащая `src/` (переопределяется `APP_HOME`).
`repo_path(*parts)` резолвит относительно неё — контейнер работает из `/app`,
а CWD может смениться.

## Database (`src/db/`)

- `base.py`: единое `MetaData` с naming convention (`ix_/uq_/ck_/fk_/pk_`) —
  без него Alembic не умеет `ALTER` на SQLite.
- `engine.py`: `create_engine`, `create_sessionmaker`, ленивые process-global
  `get_engine`/`get_sessionmaker`, `dispose_engine`, `reset_engine(engine)` —
  тестовый хук для подмены на in-memory.
- Сессия на обработчик: `handlers/deps.session_scope()` — commit при успехе,
  rollback при исключении.

## Migrations

`alembic/env.py` — async, URL строго из `DATABASE_URL` (в `alembic.ini` пусто),
`compare_type=True`, `ensure_sqlite_parent_dir` до подключения.
Правило: изменение `models.py` и миграция — в одном коммите.

## Packaging

- `Dockerfile` — multi-stage: venv в builder, non-root `app` в runtime,
  `tini` как PID 1, `MPLCONFIGDIR=/tmp/matplotlib` (иначе matplotlib пишет в `$HOME`).
- `scripts/entrypoint.sh` — `alembic upgrade head` с 3 попытками (fail-open),
  затем `python -m src.bot`.
- `docker-compose.yml` — `db` (postgres:16-alpine) → `migrate` → `bot`.
