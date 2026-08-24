# models — выбор модели владельцем и свободные модели

## Уровни выбора (`src/llm/model_selection.py`)

Слот — место, где зовётся модель:

```
SLOTS = ("vision", "text", "stt")
  vision — фото: еда, этикетка, экран CGM, бланк анализов, упаковка лекарства
  text   — свободный текст: разбор блюда, извлечение симптомов
  stt    — расшифровка голосовых
```

Два уровня, приоритет **слот > общий**, ниже — значение из `Settings`:

```
resolve(slot, stored) -> Resolved(model_id, level)     # level: slot|global|env
current(slot) -> str | None        # из процессного кэша, без обращения к БД
refresh(mapping) -> None           # положить разрешённое в кэш
```

Хранение — таблица `settings_kv` (ключ → JSON):
`model_global: str`, `models: {slot: model_id}`. Уровни живут в разных ключах:
выбор «одну модель на всё» не затирает слот, настроенный руками.

`src/vision/recognize.py` и `src/handlers/intake.py` берут модель через
`model_selection.current(slot)` — это чистый доступ к процессному кэшу,
слой распознавания в БД не ходит. Кэш заполняется на старте бота
(`load_active_models`) и после каждой смены модели.

## Каталог (`config/models.json`, в репозитории)

```
{"<slot>": {"default": "<id>",
            "models": [{"id","label","tier":"free"|"paid",
                        "usd_per_1k": float, "note": str}]}}
```

Каталог — то, что показывается в меню. К нему добавляются свободные модели из
`free_catalog` (см. ниже), помеченные `tier="free"`.

## Свободные модели (shir-man)

`src/llm/free_catalog.py`:

```
FreeModel(frozen)  id  label  provider  context_tokens  daily_quota?  notes?
refresh_free_models(*, client=None) -> list[FreeModel]        # async
  GET https://shir-man.com/api/free-llm/top-models
  → data/free_models.json {"fetched_at": iso, "models": [...]} (atomic .tmp+rename)
  5xx/таймаут → последний кэш + warning; кэша нет → FreeCatalogUnavailable
load_free_models(*, max_age_h=24, allow_refresh=True) -> list[FreeModel]  # async
```

`data/free_models.json` — рантайм-кэш, в репозиторий не коммитится.

## Фолбэк на 429 (`src/llm/fallback.py`)

Свободные модели у провайдера лимитируются постоянно; голый 429 не должен
ронять распознавание.

```
FallbackLLMClient(primary, primary_model, alternates[(client, model)])
- совместим с LLMClient (chat / vision / transcribe / aclose)
- цепочка = primary + alternates[:MAX_FALLBACKS=2]; побеждает первый успех
- переключается на LLMRateLimitError и LLMTimeoutError; прочие — пробрасывает
- цепочка исчерпана → последнее исключение
- лог: llm.fallback.switching {failed_model, next_model}
```

Цепочка **никогда** не уходит со свободной модели на платную: альтернативы
берутся только из каталога свободных.

## Команды владельца (`src/handlers/admin.py`)

Только `OWNER_TG_IDS`, только в личке. Чужому — молчание (роутер пропускает
апдейт дальше).

```
/models            таблица «слот → модель (уровень)»
/model             меню: [🌐 Все слоты] [🎯 Один слот] [🆓 Свободные]
/errors            последние отчёты об ошибках copy-paste блоком
```

Callback-грамматика (список кандидатов лежит в FSM, в callback едет индекс —
64-байтный лимит Telegram):

```
mdl:lvl:<global|slot|free> | mdl:slot:<slot> | mdl:set:<target>:<idx> | mdl:close
```
