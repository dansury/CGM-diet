# health_sync — данные с телефона и HTTP-поверхность

## Почему релей, а не API

Ни Samsung Health, ни Apple Health не дают сторонним сервисам server-to-server
доступ: данные выходят только с самого устройства. Поэтому интеграция — тонкий
релей. Что читает телефон, нам всё равно:

| Платформа | Кто отправляет | `source` |
|---|---|---|
| Android | приложение-мост `apps/health-bridge/` через Health Connect | `health_connect` |
| iPhone | «Быстрая команда» пользователя, читающая Здоровье | `healthkit` |

Эндпоинт один и тот же, тело одно и то же. Единственное, что различается, —
названия образцов: имена Apple переименовать «Быстрой команде» негде, поэтому
их приводит к нашим четырём видам `normalize_kind` (`KIND_ALIASES`).

`[TG-ONLY: релей пишет в карточку по `tg_id`, а у веб-приложения своего
telegram-id нет — и PWA всё равно не имеет доступа ни к Health Connect, ни к
HealthKit. Появится у web собственная учётная запись на телефоне — вернуться
к этому.]`

## Приложение-мост (`apps/health-bridge/`)

Android, Kotlin, minSdk 26. Читает Health Connect (шаги, тренировки, сон,
пульс) и шлёт батчи на `<base>/health/sync`. Своего сервера нет; на телефоне
хранятся только `base`, `tg_id`, `token` и граница последней отправки.

```
MainActivity   один экран: поля, разрешения, «Синхронизировать сейчас»
Prefs          настройки + разбор ссылки cgmdiet://setup?base=&tg=&token=
HealthReader   Health Connect -> Sample(kind,start,end,external_id,steps,avg_hr)
Uploader       POST /health/sync, X-Health-Token
SyncWorker     WorkManager, раз в час; окно — от прошлой отправки (первый раз 3 суток)
```

Сборка — `gradle assembleDebug`; APK собирает
`.github/workflows/health-bridge.yml` (артефакт запуска + файл релиза).
Ссылку на APK для пользователя задаёт `HEALTH_BRIDGE_URL`
(по умолчанию — releases репозитория).

## Инструкция в интерфейсе (`handlers/reports.py`)

`/health` — карточка со статусом (шаги за 7 дней, контраст «с прогулкой /
без») и кнопки `hs:how|ios|keys|app|menu`:

- `🤖 Android` — 6 шагов словами телефона Samsung: Health Connect →
  разрешения в Samsung Health → установка моста → ключи → доступ → первая
  синхронизация; хвостом — что делать, если данные перестали приходить
  (батарея «без ограничений»);
- `🍏 iPhone` — «Быстрые команды» вместо приложения: «Найти образцы Здоровья»
  → «Получить содержимое URL» (`POST <base>/health/sync`, заголовок
  `X-Health-Token`, тело с `tg_id`/`source`/`samples`) → автоматизация по
  времени суток. Своего приложения под iOS нет, и обещать его нечестно;
- `🔑 Мои ключи` — строка `cgmdiet://setup?base=…&tg=…&token=…` одним блоком
  плюс те же три поля по отдельности и полный адрес `<base>/health/sync`
  для тех, кто шлёт сам; без `HEALTH_SYNC_SECRET` — прямая просьба написать
  владельцу, а не пустой токен;
- `📦 Приложение-мост` — ссылка на APK, что делать с предупреждением
  «неизвестный источник», и где лежит исходник.

## Аутентификация (`src/health/sync.py`)

```
make_token(tg_id, secret) -> str    # HMAC-SHA256(secret, tg_id)[:32]
verify_token(tg_id, token, secret) -> bool   # compare_digest; пустой secret -> False
```

Телефон не носит серверный секрет; утёкший токен раскрывает ровно одного
пользователя; смена `HEALTH_SYNC_SECRET` отзывает все токены сразу.
Токен пользователь получает командой `/health`.

## Payload

```json
POST /health/sync
X-Health-Token: <token>
{
  "tg_id": 111222333,
  "source": "health_connect",
  "samples": [
    {"kind":"steps","start":"2026-08-24T08:00:00Z","end":"2026-08-24T08:15:00Z",
     "steps":420,"external_id":"hc-1"},
    {"kind":"workout","start":"2026-08-24T18:00:00Z","end":"2026-08-24T18:40:00Z",
     "kcal":210,"distance_m":3200,"avg_hr":118,"external_id":"hc-2"}
  ]
}
-> {"accepted": 2, "received": 2}
```

`kind ∈ {steps, workout, sleep, heart_rate}` — читается из `kind` или `type`;
имена Apple и Health Connect приводятся к ним через `KIND_ALIASES`
(`stepCount`, `HKQuantityTypeIdentifierStepCount`, `sleepAnalysis`,
`heartRate`, `ExerciseSession`…). Неизвестные молча отбрасываются.
`start`/`end` — ISO-8601 или epoch (сек/мс); без `end` берётся бакет 15 минут.
Лимит 5000 записей на запрос. Идемпотентность по `(user_id, external_id)`.
Ошибки: 400 — некорректный payload, 403 — токен.

## HTTP (`src/web/app.py`)

```
GET  /health              -> {status, env, db{ok,detail}, llm{mock,configured}, bot_mode}
                             200 / 503 при недоступной БД
POST /telegram/webhook    -> проверка X-Telegram-Bot-Api-Secret-Token, feed_update
POST /health/sync         -> приём активности
POST /health/samsung      -> тот же обработчик, путь первого моста
```

`/health/samsung` — путь, с которым мост уехал на телефоны, и обновлять себя
он не умеет, поэтому остаётся навсегда (`include_in_schema=False`: в схеме
эндпоинт один).

Вебхук регистрируется на старте, если задан `WEBHOOK_BASE_URL`. Без
`TELEGRAM_BOT_TOKEN` маршрут телеграма не создаётся — приложение остаётся
пригодным для приёма активности и healthcheck.

## Использование в аналитике

`repo.load_activity_buckets` → `analytics.activity.contrast_by_activity`:
сравнение подъёма после еды с прогулкой (≥ 1000 шагов за час) и без.
Результат показывается в `/health` и учитывается в `/stats`.

Записи `kind="sleep"` идут в `repo.load_sleep_intervals` →
`analytics.sleep.nights_from_intervals`: ночи, режим и связь с калориями и
сахаром следующего дня. Разбор — в `/sleep`, детали — `spec/sleep.md`.
Стадия сна (`stage`), если мост её прислал, остаётся в `payload`; стадии
«бодрствование» ночь не удлиняют.
