# web — лендинг и веб-приложение (PWA), деплой простым копированием

Каталог `web/` — отдельный деплой-таргет: обычный PHP-хостинг без Composer и
без шага сборки. Всё содержимое `web/` копируется на хостинг как есть.
Источники: `web/index.php` (лендинг) → `web/app/` (сам PWA) → `web/admin/`
(закрытая админка) → `web/api/*.php` (бэкенд) → `web/lib/` (общий код).

LLM-слой (`llm.php`, `config.php`, `settings_store.php`, `model_catalog.php`)
**не форкается**: канонический источник — репозиторий `site_yacloud_openrouter`
(его `spec.md` § Provenance). `web/lib/vendor/` — зеркало этих файлов на момент
последней синхронизации (см. `web/lib/vendor/VENDORED_FROM.md`); правки идут
сначала в `site_yacloud_openrouter`, потом копируются сюда.

## Структура

```
web/
  index.php                 лендинг: тёмная/светлая тема, Я.Метрика, CTA → /app/
  css/ tokens.css landing.css    общие токены + вёрстка лендинга
  js/  motion.js romanesco.js landing.js   пружины/скролл, фрактал, поведение лендинга
  assets/ favicon.svg fonts/electrolize.woff2
  romanesco.html            отдельная страница-визуализация, ни с чем не связана
  app/                      PWA
    index.html manifest.webmanifest sw.js
    css/app.css
    js/ app.js theme.js db.js onboarding.js camera.js recognize.js thinking.js
       dictionary.js charts.js settings.js telemetry.js push.js sync.js
  admin/                    закрыто паролем, доступно только по /admin
    index.php login.php logout.php lib.php
  api/                      JSON-эндпоинты, без сессий кроме sync/register
    _bootstrap.php recognize.php telemetry.php register.php login.php sync.php
    push_subscribe.php push_unsubscribe.php push_send.php vapid_public_key.php
  lib/
    db.php                  PDO SQLite, схема + миграции (CREATE TABLE IF NOT EXISTS)
    webpush.php             VAPID JWT (ES256) + шифрование aes128gcm (RFC 8291/8292)
    vendor/                 зеркало site_yacloud_openrouter (llm.php, config.php, …)
  .htaccess                 запрет доступа к data/ и lib/
  data/                     app.db (SQLite), гитигнор
  README.md                 инструкция по деплою
```

## Хранение

### На устройстве (IndexedDB, `js/db.js`), вечно

Данные пользователя живут **на устройстве**, без обязательной регистрации:

```
meals        id ts photoAnalyzed:bool items:[{name,grams,kcal,protein,fat,carbs}]
             totalKcal note source(camera|upload|text)
glucose      id ts mmol source(manual)
weight       id ts kg
wellbeing    id ts score(1..5) symptoms:[str] note
dictionary   id kind(meal|item|product) label grams? macros? hits lastUsedAt pinned
profile      onboarded:bool focus[] age heightCm sex conditions mealsPerDay
             goalWeightKg diabetes glucoseMethods
settings     theme(auto|light|dark) quickCamera:bool remindersEnabled:bool
             clientId(uuid) syncToken?
```

Никогда не хранится: сама фотография еды (принцип конституции III/IV
перенесён с бота — хранится только распознанный результат, фото уходит на
сервер один раз транзитом в `recognize.php` и нигде не сохраняется, ни на
клиенте, ни на сервере — см. § Распознавание).

«Очистить мои данные» (`settings.js`) — `indexedDB.deleteDatabase` + очистка
`localStorage`, необратимо, без подтверждения сервером.

### На сервере (SQLite, `web/lib/db.php`)

```
users            id email_hash password_hash sync_token created_at
backups          user_id payload(JSON) updated_at            -- полный слепок IndexedDB
telemetry_events id client_id user_id? kind payload(JSON) utm_source utm_medium
                 utm_campaign utm_term utm_content ts
push_subscriptions id client_id user_id? endpoint p256dh auth reminders_enabled
                 created_at
admin_config     -- таблица settings из vendor/settings_store.php (общая с site_yacloud)
```

Телеметрия собирается со **всех** пользователей независимо от регистрации:
`clientId` — случайный UUID, заведённый при первом запуске (`localStorage`),
отправляется с каждым событием. Регистрация только даёт `user_id`, которым
телеметрия дополнительно помечается (объединять посетителя и пользователя
после регистрации разрешено — тот же UTM/визит).

## Онбординг (`js/onboarding.js`)

Один раз при первом запуске (`!settings.onboarded`), до появления камеры.
Поля — те же, что в анкете бота (`spec/onboarding.md` § Шаги), в том же
порядке и с той же логикой пропуска и разветвления по полу/целям:
`focus → age → height → weight → sex(→pregnant) → conditions → meals → goal`,
плюс сахарный трек (`dia → dia_meds → sugar_method`), если среди целей
отмечено `sugar`. Каждый шаг можно пропустить. Экран — один вопрос на шаг,
как в боте; хранится в `profile` (IndexedDB), не отправляется на сервер, пока
пользователь сам не зарегистрируется (тогда уходит вместе с `backups`).

Одиночный выбор (`пол`, `беременность`, `приёмы пищи`) — `choiceRow(options)`:
нажатая кнопка получает класс `selected` (стиль `.btn.selected` в
`app/css/app.css`), остальные его теряют. Приёмов пищи — `MIN_MEALS_PER_DAY=1`
… `MAX_MEALS_PER_DAY=7`, те же границы, что у бота
(`src/analytics/plate.py`, `spec/plate.md` § Сколько приёмов пищи в день).

## Распознавание еды (`js/recognize.js` → `api/recognize.php`)

1. Клиент получает фото (камера/загрузка) или текст, **не сохраняя фото
   локально** — сразу base64 в память, один `fetch POST` на `/api/recognize.php`.
2. `recognize.php`: `LLM::visionJson(system, userText, [dataUrl], …)` (фото)
   либо `LLM::chatJson(system, userText, …)` (только текст) —
   `web/lib/vendor/llm.php`. Промпт просит те же поля, что и бот
   (`spec/ingest.md` § food_photo): название блюда, позиции, вес, БЖУК,
   уверенность.
3. Ответ уходит клиенту и **не пишется на сервере** — ни фото, ни JSON;
   единственный серверный след — телеметрия `kind=meal_recognized` без
   данных о составе (только факт и задержка). Сохранение — на клиенте
   (`db.js` → `meals`).
4. Подтверждение — как у бота (принцип IV): карточка с разбором, вес и БЖУК
   редактируются перед сохранением, а не после.

## Словарь (`js/dictionary.js`)

Живой автокомплит: после 2 введённых символов — фильтр `dictionary` из
IndexedDB по `label` (префикс, затем подстрока), ротация та же, что у бота
(`spec/dictionary.md` § Ротация): `pinned desc, lastUsedAt desc, hits desc`.
Выбор подсказки заполняет карточку без обращения к модели.
`exampleLabels({kinds:["item","meal"], limit})` — названия для примеров в
подсказках, аналог `repo.example_labels` бота (`spec/bot.md` § Примеры в
подсказках): те же виды, пороги и ротация. Поле быстрого ввода на экране
съёмки показывается **только если список непустой**, и placeholder берёт из
него имя («Начните вводить название — «сырники»…»): обещать подсказки пустому
словарю и звать вымышленной «овсянкой» нечестно. У каждой
подсказки — редактируемое поле веса (граммы), меняющее БЖУК пропорционально
(масштаб от `grams`, как `nutrition.apply_memory` у бота). Порог показа в
словаре — тот же `MIN_HITS` (`meal`/`item`: 2, `product`: 1).

`[WEB-ONLY GAP: medication/symptom]` — виды `medication` и `symptom`
(`spec/dictionary.md`) в web-словаре пока не реализованы: у бота нет
аналога веб-сценария «лекарство/симптом одним тапом» без остального UI
(`/meds`, `WellbeingFlow`), которого в web MVP ещё нет вовсе. Задача на
реализацию — `TODO.md` T071.

## Телеметрия (`js/telemetry.js`)

При первом запуске: `utm_source/medium/campaign/term/content` из
`location.search`, `firstVisitAt`. `track(kind, payload?)` — `sendBeacon`
(фолбэк `fetch keepalive`) на `/api/telemetry.php`. События: `first_visit`,
`onboarding_step`, `onboarding_done`, `meal_recognized`, `camera_opened`,
`dictionary_used`, `chart_viewed`, `push_subscribed`, `push_denied`,
`push_sent` (уходит из `push_send.php`), `push_clicked` (из `sw.js`
`notificationclick`), `data_cleared`, `registered`.

## Push-уведомления (`js/push.js`, `sw.js`, `api/push_*.php`, `lib/webpush.php`)

Включаются тумблером в настройках «Напоминания о регулярности» (аналог
`users.glucose_prompt_enabled` у бота, `spec/onboarding.md` § Предложение
присылать замеры) — по умолчанию выключены. Включение → `Notification.
requestPermission()` → `serviceWorker.pushManager.subscribe({applicationServerKey:
VAPID_PUBLIC})` → `POST /api/push_subscribe.php`.

`lib/webpush.php` — VAPID (RFC 8292, JWT ES256 через `openssl_sign`) +
шифрование пейлоада `aes128gcm` (RFC 8291, ECDH P-256 + HKDF-SHA256 +
AES-128-GCM через `openssl_pkey_derive`/`hash_hkdf`/`openssl_encrypt`, PHP ≥
8.1). VAPID-ключи генерируются один раз и хранятся в `admin_config`
(admin-панель может перегенерировать).

`push_send.php?key=<CRON_SECRET>` — рассылает напоминания подписчикам с
`reminders_enabled=1` (по расписанию — cron хостинга, дергающий этот URL;
общего воркера на shared-хостинге нет) и кнопка «отправить сейчас» в
админке.

**[BLOCKED: сквозная проверка push нужна на реальном браузере/устройстве —
в среде сборки нет push-сервиса для end-to-end теста]**, как и мост Samsung
Health (`DEV_PLAN.md` фаза 9): код собран и соответствует спецификации,
`php -l` зелёный, живой пуш не прогонялся.

## Регистрация и синхронизация (`js/sync.js`, `api/register.php`, `api/sync.php`)

Полностью опциональна. `register.php`: email + пароль (`password_hash`),
возвращает `sync_token` (случайный, хранится в `settings.syncToken`).
`sync.php` (`Authorization: Bearer <token>`): `PUT` — кладёт весь слепок
IndexedDB (`JSON.stringify` всех store) в `backups.payload`; `GET` — отдаёт
его обратно (для нового устройства). Слияние — «сервер побеждает» при `GET`
на новом устройстве, «клиент побеждает» при explicit `PUT` — конфликтов
между устройствами не разрешаем (одно активное устройство на пользователя).

## Тема (`js/theme.js`)

`prefers-color-scheme` — источник по умолчанию; ручной оверрайд в настройках
(`settings.theme = light|dark`) переопределяет системную тему через
`data-theme` на `<html>`, `auto` снимает атрибут. Оба лендинг и приложение
используют одни и те же CSS-переменные (`--bg`, `--fg`, `--accent`, …).
Смена темы вручную дёргает `window.romanescoBg.refresh()` — фон перечитывает
`--rom-*` и перерисовывается.

## Визуальный язык

Светлый минимализм, ведущий цвет — бирюзовый. Дисплейный шрифт Electrolize
(локальный `assets/fonts/electrolize.woff2`, SIL OFL), интерфейсный — Inter с
системным фолбэком. Шаг сетки и типографики — по золотому сечению (`--s1..--s8`
= 5 8 13 21 34 55 89 144). Токены — `css/tokens.css`, оба лендинга и
приложение читают одни и те же переменные.

`[WEB-ONLY: у Telegram-бота нет собственной поверхности отрисовки — оформление
сообщений задаёт клиент Telegram. Паритет tg/web (CLAUDE.md #11) на визуальный
язык не распространяется; палитра графиков бота (`spec/charts.md`) остаётся
семантической — цвет там означает уверенность и целевой коридор, а не бренд.]`

### Романеско (`js/romanesco.js`)

Фрактальная эмблема продукта — кочан романеско, нарисованный, а не картинка.
Один кочан = поле бугорков на логарифмической спирали: k-й бугорок под углом
`k·GA` (`GA` = золотой угол 137,5°) на расстоянии `G^k` (`G` = 1,0405) от
вершины, `k` от 0 (обод) внутрь. Радиус бугорка = 0,315 расстояния; глаз
читает из этой одной последовательности парастихи 8/13/21 — как на овоще.

Каждый бугорок — снова кочан. Рекурсия обрывается по размеру на экране, а не
по глубине (`OPEN_AT` = 22 px, `MAX_DEPTH` = 4, бюджет узлов на кадр): бугорок
«раскрывается» только когда становится достаточно крупным, поэтому при
приближении фигура разворачивается, а не просто растёт. Индекс бугорка входит
в поворот вложенного кочана — картинка не повторяется буквально.

`Romanesco.mount(canvas, opts) -> {refresh(), setProgress(p), destroy()}`;
`opts`: `mode`, `eyeX`, `eyeY`, `fill` (радиус кочана / `min(W,H)`), `alpha`,
`budget`. Цвет берётся из `--rom-ink`/`--rom-tint`/`--rom-alpha` (rgb-тройки),
то есть следует теме. `prefers-reduced-motion` — один статичный кадр.

Режимы:

| mode | где | поведение |
|---|---|---|
| `scroll` | фон лендинга (`#rom-bg`) | увеличение ×1 → ×2,6 по прогрессу скролла, параллакс от курсора |
| `ambient` | фон приложения (`#rom-bg`), витрина в § «Данные» лендинга | медленное дыхание, низкий контраст, 20 fps |
| `thinking` | ожидание ответа модели | дыхание ×2 за 15 с, поворот 0,05 рад/с |

`js/motion.js` — общий слой: пружина, один rAF-тикер на страницу, прогресс
скролла, `reveal()` со стаггером.

### Ожидание модели (`app/js/thinking.js`)

`showThinking(container) -> {destroy()}` — занимает весь экран вместо формы
съёмки на время `recognize.php`: кочан в круглом «иллюминаторе» (растёт за
края), строка статуса, меняющаяся раз в 3,6 с (что делает модель + факты про
фрактал), и пометка «фото никуда не сохраняется». Пока экран открыт, на
`<body>` висит `is-waiting` — фоновый кочан приглушается, чтобы не спорить с
тем, за которым наблюдают. Вызывается из `app/js/camera.js` на обеих ветках
(фото и текст); при ошибке — `destroy()`, тост и возврат в форму съёмки с
сохранённым текстом.

### Метка и иконки

`assets/favicon.svg` — тот же кочан в 32-й сетке. `app/icons/*.png` рисует
`scripts/gen_app_icons.py` (только стандартная библиотека: аналитическая
растеризация в супер-сэмплированный альфа-буфер + собственный писатель PNG),
геометрия — та же, что в `romanesco.js`. Инлайновый `<symbol id="mark">` в
`index.php` и `app/index.html` — 38 окружностей той же спирали, используется в
шапке и в списке возможностей.

## Быстрая камера

Настройка «Открывать камеру сразу» (`settings.quickCamera`): при `true`
`app.js` на старте (после онбординга) сразу показывает экран съёмки вместо
списка приёмов пищи. Файл выбора — `<input type=file accept=image/* capture=
environment>` либо `getUserMedia`, результат конвертируется в JPEG в памяти
(`canvas.toBlob`) и не пишется в `IndexedDB`/файловую систему устройства —
только транзитом в `recognize.php` (см. § Распознавание).

## Yandex.Metrika

Сниппет (счётчик 112333821) вставлен как есть в `web/index.php` и
`web/app/index.html`, `head`, без изменений кода счётчика.

## Админка (`web/admin/`)

Доступна только по пути `/admin`, гейт — пароль (`ADMIN_PASSWORD` из
`admin_config`/ENV, `password_hash`/`password_verify`, сессия). Разделы:
переменные — форма поверх `SettingsStore` (`web/lib/vendor/settings_store.php`,
та же таблица `settings`, что у `site_yacloud_openrouter`); статистика —
счётчики телеметрии (визиты, UTM, использование функций,
доставленные/кликнутые пуши), список зарегистрированных пользователей
(без паролей).

### Модель / LLM

Слаг модели руками не набирают — выпадающие списки собираются из
`config.AVAILABLE_MODELS` (вшитый список + живой каталог провайдеров,
`ModelCatalog`), как в `setup.php` у `site_yacloud_openrouter`:

```
LLM_PROVIDER            select  openrouter | yandex
LLM_PROVIDER_PRIORITY   select  openrouter,yandex | yandex,openrouter
LLM_DEFAULT_MODEL       select  <optgroup group> по id; ею же распознаётся фото
LLM_VISION_MODEL        select  только openrouter-строки, по full_id; PDF/OCR
LLM_FALLBACK_MODE       select  auto (новее той же модели) | manual
LLM_FALLBACK_MODELS     text    короткие id через запятую
MODEL_CATALOG_TTL_MIN   text    срок годности кэша каталога, мин
OPENROUTER_API_KEY      password  пустое поле не стирает сохранённый ключ
YANDEX_API_KEY          password  то же
```

Каждый вариант подписан ценой: вшитые строки — ₽ за 1k, живые — $ за 1M
(так их отдаёт провайдер, курс не выдумывается). Сохранённое значение,
которого нет в каталоге, остаётся в списке отдельной строкой «нет в каталоге»
— иначе браузер выбрал бы первую и сохранение молча сменило бы модель.

Каталог обновляется сам при заходе на страницу, если кэш старше
`MODEL_CATALOG_TTL_MIN` (`ModelCatalog::maybeRefresh`); кнопки «Обновить
каталог моделей» / «Забыть живой каталог» (`POST model_catalog=refresh|forget`)
делают это вручную. Сеть недоступна — страница не ломается: остаётся прежний
кэш, причина показана под списками.

## `romanesco.html` — 3D-визуализация капусты романеско [WEB-ONLY: холст WebGL, в чате бота показать нечего]

Самостоятельная страница без зависимостей и без ссылок с лендинга и из PWA:
открывается только по прямому адресу `/romanesco.html`. Ни телеметрии, ни
сети, ни хранения — чистый рендер. Не путать с `web/js/romanesco.js` — тем
плоским фоновым кочаном из § Визуальный язык; общего кода у них нет.

Модель. Головка — конус `h = H·(1 − (r/R)^pw)`, соцветия на нём расставлены
филлотаксисом: `θ_i = φ + i·GA` (`GA` — золотой угол 137.507°),
`r_i = R·e^(−λ·i)`, где `λ = ln(1/rmin)/N` задаётся числом соцветий `N`.
Радиус соцветия = `½·minDist(λ)·pack`, где `minDist` — расстояние до
ближайшего соседа по спирали (перебор смещений `m`, самоподобие даёт один
расчёт на весь конус). Каждое соцветие рекурсивно повторяет ту же схему:
ось = нормаль поверхности с наклоном `tilt` к вершине родителя, базис
`(u,v,n)` — от родителя. Уровень 1 — только головка, 2 — головка + бутоны,
3 — плюс третий порядок; счётчики режутся до `MAX_LEAVES = 32000`.

Геометрия. Каждый конус — кольца вращения `[доля радиуса, доля высоты, AO,
смесь цвета]` + вершина: боковые полосы триангулируются в индексный буфер,
нормали берутся из наклона поверхности (гладкая штриховка). Внутри каждого
конуса — тёмное «тело» (`coreRings`), оно закрывает просветы между
соцветиями; у головки к нему добавлена юбка ниже плоскости основания и
донышко, иначе снизу видно нутро.

Рендер. Один `drawElements` на кадр, WebGL2 (или WebGL1 + `OES_element_index_uint`).
Скрытые поверхности убирает z-буфер, задние грани — `CULL_FACE`; порядок
отрисовки не важен. Освещение per-pixel: обёрнутый диффуз + холодная
подсветка с теневой стороны + rim + блик (гасится на тёмном «теле» через
`gloss`) + туман по глубине. Три оттенка зелёного развешены по семействам
парастих (`ancestor % 13 % 3`) — от этого спирали читаются глазом.

Управление. Автовращение по рысканью, перетаскивание — орбита, колесо и
щипок — зум, пробел — пауза; ползунки числа соцветий, глубины, скорости и
угла света; «Сброс» и «PNG» (`preserveDrawingBuffer` держит буфер живым).
На экране уже 720 px модель строится легче, панель стартует свёрнутой.

## Паритет tg/web

См. `CLAUDE.md` § Паритет tg/web — новая функциональность, добавленная в
одну версию, обязана появиться и в другой (или получить явную пометку
`[WEB-ONLY]`/`[TG-ONLY]` с причиной в этой же спеке).
