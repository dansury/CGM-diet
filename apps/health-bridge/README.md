# CGM Мост — приложение для Samsung Health

Samsung Health не отдаёт данные серверам напрямую: они уходят с телефона
только через **Health Connect**. Это приложение читает Health Connect и
отправляет шаги, тренировки, сон и пульс на адрес, который ввёл пользователь.
Своего сервера, рекламы и аналитики у него нет.

## Что делает

```
Samsung Health → Health Connect → CGM Мост → POST <base>/health/samsung
                                             X-Health-Token: <token>
```

- окно чтения — от прошлой отправки (первый раз — 3 суток) до «сейчас»;
- повторная отправка безопасна: сервер отсеивает по `external_id`;
- расписание — раз в час (`WorkManager`), плюс кнопка «Синхронизировать сейчас»;
- на телефоне не хранится ничего, кроме адреса, ID и токена.

Формат запроса — `spec/health_sync.md` § Payload.

## Настройка на телефоне

1. Health Connect. Android 14+: *Настройки → Безопасность и конфиденциальность
   → Ещё → Health Connect*. Android 10–13: «Health Connect» из Galaxy Store
   или Google Play.
2. *Samsung Health → ☰ → Настройки → Health Connect*: включить шаги,
   тренировки, сон, пульс.
3. Поставить APK (см. ниже), открыть, нажать «Вставить строку настройки» —
   строку `cgmdiet://setup?base=…&tg=…&token=…` даёт бот по `/health` →
   «🔑 Мои ключи».
4. «Разрешить доступ к Health Connect» → «Синхронизировать сейчас».
5. Если данные перестали приходить: *Настройки → Приложения → CGM Мост →
   Батарея → Без ограничений*.

## Сборка

Wrapper в репозитории не хранится — нужен установленный Gradle 8.7+ и JDK 17:

```
cd apps/health-bridge
gradle assembleDebug      # app/build/outputs/apk/debug/app-debug.apk
```

Готовый APK собирает GitHub Actions (`.github/workflows/health-bridge.yml`):
артефакт `cgm-bridge-apk` у каждого запуска, `cgm-bridge.apk` — у каждого
релиза. Ссылку, которую бот показывает пользователю, задаёт переменная
`HEALTH_BRIDGE_URL`.

## Файлы

| Файл | Что в нём |
|---|---|
| `MainActivity.kt` | один экран: поля, разрешения, ручная синхронизация |
| `Prefs.kt` | адрес, ID, токен, граница последней отправки, разбор `cgmdiet://` |
| `HealthReader.kt` | чтение Health Connect → `Sample` |
| `Uploader.kt` | `POST /health/samsung`, единственный сетевой вызов |
| `SyncWorker.kt` | часовое расписание и одна синхронизация |
