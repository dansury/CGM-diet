# errors — отчёты об ошибках: админу подробно, пользователю коротко

Правило: любая необработанная ошибка на любой поверхности (бот, HTTP, фоновая
задача, лог) уходит владельцу **одним** сообщением, тело которого — единый
copy-paste блок, готовый к вставке в баг-трекер или в Claude Code.
Пользователь видит одну короткую фразу и не видит трейсбека никогда.

## API (`src/errors_report.py`)

```
ErrorReport(frozen)  source("bot"|"web"|"task"|"log")  where  error
                     traceback?  user?  context{str:str}  ts(UTC)

fingerprint(report) -> str        # sha1("source|where|exc|последний кадр")[:12]
render_report(report, *, repeats=0, suppressed=0, limit=3500) -> str
report_error(*, source, where, exc=None, error=None, traceback_text=None,
             user=None, context=None) -> str      # async
    -> "sent"|"deduped"|"throttled"|"disabled"|"no_recipients"|"failed"|"noise"
report_error_nowait(**kwargs) -> None             # fire-and-forget
is_transient_noise(where, error) -> bool
wire_error_reporter(bot, settings) -> None        # + set_error_forwarder
reset_error_reporter() -> None                    # тест-хук
forward_log_event(record) -> None                 # ERROR+ из logging → отчёт
recent_reports(limit=10) -> list[ErrorReport]
```

## Вид сообщения

```
🔴 <b>{source}</b> · <code>{where}</code>  (+ « ×N» при повторах)
<pre><code class="language-log">…</code></pre>
```

`<pre><code>` — Telegram рисует его с кнопкой «копировать», поэтому отчёт
никогда не режется на два сообщения. Тело блока по порядку:

```
CGM-diet error [{fingerprint}]
time / source / where / user / <ключ: значение из context, по строке>
error: {error}

{traceback дословно}
```

В `limit` укладываемся вырезанием **середины** трейсбека
(`… N строк пропущено …`): нужны и точка входа, и место броска.
Всё интерполируемое экранируется HTML.

## Дедуп и троттлинг

- один `fingerprint` — не чаще раза в `DEDUPE_WINDOW_S = 300`; повторы внутри
  окна копятся в счётчик и приезжают как `×N` / `repeats: N since <ts>`;
- глобальный потолок `MAX_PER_MINUTE = 8`, сверх него — `"throttled"`,
  число уносится в следующий отчёт как `suppressed: N (rate limit)`;
- счётчики в процессе, рестарт их обнуляет.

## Шум, который не шлём

`is_transient_noise` — то, что бот переживает сам (Telegram 5xx, сетевые
таймауты, 429 у модели с последующим фолбэком на свободную). Совпадение
подстроки по `"<where>\n<error>"`, регистр не важен. Такие события остаются в
логах и в `/errors`, но владельца не будят.

Не пересылаются также: WARNING и ниже; события логгеров `errors_report.*`,
`handlers.errors.*` (иначе отчёт об отчёте зациклится).

## Точки подключения

| Поверхность | Хук | source / where |
|---|---|---|
| любой хендлер aiogram | `src/handlers/errors.py` — `router.errors` | `bot` / `<модуль>.<функция>` |
| HTTP (вебхук, health-sync) | `src/web/app.py` — `error_report_middleware` | `web` / `<METHOD> <path>` |
| ERROR+ в логах (fail-soft пути) | `logging_setup.set_error_forwarder` | `log` / `<logger> (<модуль>:<строка>)` |

`src/handlers/errors.py` отвечает пользователю ровно один раз и мягко:
«⚠️ Что-то пошло не так. Ваши записи целы, мы уже видим ошибку и чиним».
Возвращает `True` — aiogram не логирует трейсбек повторно.

## Кольцевой буфер и `/errors`

`logging_setup` держит `recent_errors(limit)` — последние 50 событий WARNING+.
Команда `/errors` (только владелец, только в личке) отдаёт последние отчёты
тем же copy-paste блоком.

## Настройки

```
error_reports_enabled: bool = True          # ERROR_REPORTS_ENABLED
error_report_tg_ids: tuple[int, ...] = ()   # ERROR_REPORT_TG_IDS, пусто → owner_tg_ids
```
