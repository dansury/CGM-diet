# health_sync — Samsung Health и HTTP-поверхность

## Почему релей, а не API

Samsung Health не предоставляет сторонним сервисам server-to-server доступ.
Данные покидают телефон только через **Health Connect** на самом устройстве.
Поэтому интеграция — тонкий релей: приложение-мост на телефоне читает Health
Connect и отправляет батчи на наш эндпоинт. Формат платформо-нейтральный, та же
схема без изменений подходит для Apple HealthKit.

## Приложение-мост (`apps/health-bridge/`)

Android, Kotlin, minSdk 26. Читает Health Connect (шаги, тренировки, сон,
пульс) и шлёт батчи на `<base>/health/samsung`. Своего сервера нет; на телефоне
хранятся только `base`, `tg_id`, `token` и граница последней отправки.

```
MainActivity   один экран: поля, разрешения, «Синхронизировать сейчас»
Prefs          настройки + разбор ссылки cgmdiet://setup?base=&tg=&token=
HealthReader   Health Connect -> Sample(kind,start,end,external_id,steps,avg_hr)
Uploader       POST /health/samsung, X-Health-Token
SyncWorker     WorkManager, раз в час; окно — от прошлой отправки (первый раз 3 суток)
```

Сборка — `gradle assembleDebug`; APK собирает
`.github/workflows/health-bridge.yml` (артефакт запуска + файл релиза).
Ссылку на APK для пользователя задаёт `HEALTH_BRIDGE_URL`
(по умолчанию — releases репозитория).

## Инструкция в интерфейсе (`handlers/reports.py`)

`/health` — карточка со статусом (шаги за 7 дней, контраст «с прогулкой /
без») и кнопки `hs:how|keys|app|menu`:

- `📲 Как подключить` — 6 шагов словами телефона Samsung: Health Connect →
  разрешения в Samsung Health → установка моста → ключи → доступ → первая
  синхронизация; хвостом — что делать, если данные перестали приходить
  (батарея «без ограничений»);
- `🔑 Мои ключи` — строка `cgmdiet://setup?base=…&tg=…&token=…` одним блоком
  плюс те же три поля по отдельности; без `HEALTH_SYNC_SECRET` — прямая
  просьба написать владельцу, а не пустой токен;
- `📦 Приложение-мост` — ссылка на APK, что делать с предупреждением
  «неизвестный источник», и где лежит исходник.

## Аутентификация (`src/health/samsung.py`)

```
make_token(tg_id, secret) -> str    # HMAC-SHA256(secret, tg_id)[:32]
verify_token(tg_id, token, secret) -> bool   # compare_digest; пустой secret -> False
```

Телефон не носит серверный секрет; утёкший токен раскрывает ровно одного
пользователя; смена `HEALTH_SYNC_SECRET` отзывает все токены сразу.
Токен пользователь получает командой `/health`.

## Payload

```json
POST /health/samsung
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

`kind ∈ {steps, workout, sleep, heart_rate}`; неизвестные молча отбрасываются.
`start`/`end` — ISO-8601 или epoch (сек/мс); без `end` берётся бакет 15 минут.
Лимит 5000 записей на запрос. Идемпотентность по `(user_id, external_id)`.
Ошибки: 400 — некорректный payload, 403 — токен.

## HTTP (`src/web/app.py`)

```
GET  /health              -> {status, env, db{ok,detail}, llm{mock,configured}, bot_mode}
                             200 / 503 при недоступной БД
POST /telegram/webhook    -> проверка X-Telegram-Bot-Api-Secret-Token, feed_update
POST /health/samsung      -> приём активности
```

Вебхук регистрируется на старте, если задан `WEBHOOK_BASE_URL`. Без
`TELEGRAM_BOT_TOKEN` маршрут телеграма не создаётся — приложение остаётся
пригодным для приёма активности и healthcheck.

## Использование в аналитике

`repo.load_activity_buckets` → `analytics.activity.contrast_by_activity`:
сравнение подъёма после еды с прогулкой (≥ 1000 шагов за час) и без.
Результат показывается в `/health` и учитывается в `/stats`.
