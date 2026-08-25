# sleep — сколько спится, насколько ровно и что бывает в такие дни

Два независимых источника ночей, один и тот же `SleepNight` на выходе.
Health Connect точнее и потому приоритетнее; наблюдение по появлениям —
запасной вариант для тех, кто Samsung Health не подключал.

## Почему появления, а не «был(а) в сети»

Bot API **не отдаёт ботам** статус онлайн и «был(а) недавно»: поля нет в
апдейте вовсе, и никакая настройка приватности пользователя его не открывает
(«Последняя активность» управляет видимостью для людей, не для ботов).
Поэтому «появление» — это апдейт, который человек сам прислал: сообщение,
нажатие кнопки, фото. Ночи из таких отметок помечаются `estimated`, и карточка
прямо говорит, что оценка приблизительная.

## Модель (`src/analytics/sleep.py`)

```
SleepInterval(start_at, end_at, stage?)        PresenceSession(start_at, end_at, pings)
SleepNight(date, bedtime, wake_at, duration_min, source(health|presence),
           segments, estimated) ; .short = duration_min < SHORT_SLEEP_MIN
SleepStats(n_nights, mean_duration_min, median_duration_min, short_nights,
           bedtime_sd_min, wake_sd_min, median_bedtime_min, median_wake_min, source)
           ; .regular = обе SD ≤ REGULAR_SD_MIN
SleepContrast(metric(kcal|carbs|glucose|rise), label_a, label_b, n_a, n_b,
              mean_a, mean_b, difference, ci_low, ci_high, p_value)
              ; .meaningful = по ≥ 3 дня в каждой группе
DayIntake(date, kcal, carbs_g, meals)          SleepReport(stats, nights[], contrasts[])
```

Пороги: `SEGMENT_GAP_MIN=60`, `MIN_NIGHT_MIN=120`, `MAX_NIGHT_MIN=840`,
`DAY_START_HOUR=4`, `SHORT_SLEEP_MIN=390`, `IRREGULAR_SHIFT_MIN=90`,
`REGULAR_SD_MIN=60`, `PRESENCE_SILENCE_DAYS=1`.

## Ночи из Health Connect

```
merge_intervals(intervals, gap_min=60) -> [SleepInterval]
nights_from_intervals(intervals, tz) -> [SleepNight]
```

1. стадии из `AWAKE_STAGES` (`awake, awake_in_bed, out_of_bed, unknown`)
   отбрасываются — они не должны удлинять ночь;
2. записи, отстоящие меньше чем на `SEGMENT_GAP_MIN`, сливаются в один
   отрезок: сессия и её стадии приезжают отдельными строками;
3. отрезок вне `[MIN_NIGHT_MIN; MAX_NIGHT_MIN]` — дневной сон или битая
   запись, в ночи не идёт;
4. ночь ключуется **локальной датой пробуждения**; на дату остаётся самая
   длинная — утренний сон не вытесняет ночь.

## Ночи из появлений

```
presence_sessions(pings, gap_min=30) -> [PresenceSession]
is_significant(session, tz) -> bool
nights_from_presence(pings, tz) -> [SleepNight]
days_since_presence(pings, now) -> float|None
```

Появления с разрывом ≤ `SESSION_GAP_MIN` — одна сессия. Сессия считается
«человек не спит», если:

| Когда началась | Порог |
|---|---|
| вне `NIGHT_CORE` (00:00–05:00) | ≥ `MIN_SESSION_PINGS` (2) отметки **или** ≥ `MIN_SESSION_SPAN_MIN` (10) минут |
| внутри `NIGHT_CORE` | ≥ `NIGHT_MIN_PINGS` (3) отметки **и** ≥ `NIGHT_MIN_SPAN_MIN` (25) минут |

Ночью планка выше намеренно: посмотреть время в три часа ночи — не
пробуждение. Одиночная ночная отметка не разрывает ночь; выдержавшая порог
сессия относится к суткам, которые начались в `DAY_START_HOUR`, то есть
считается «ещё не лёг» и укорачивает ночь.

Подъём = начало первой значимой сессии локальных суток, отбой = конец
последней значимой сессии предыдущих суток. Ночь публикуется, только если
разрыв укладывается в `[MIN_NIGHT_MIN; MAX_NIGHT_MIN]`.

## Режим

```
summarize(nights, tz) -> SleepStats
split_by_duration(nights, threshold_min=SHORT_SLEEP_MIN) -> (короткие, обычные)
split_by_regularity(nights, tz, shift_min=IRREGULAR_SHIFT_MIN) -> (сдвинутые, привычные)
clock_label(minutes) / duration_label(minutes)
```

Время отбоя и подъёма усредняется **по кругу**: 23:50 и 00:10 отстоят на 20
минут, а не на 23 часа. SD — круговая (`sd = sqrt(-2·ln R)`), медиана — по
минимальному суммарному отклонению на окружности. «Сдвинутая» ночь считается
от собственной медианы отбоя: «поздно» у совы и у жаворонка — разное время.
Разбиение по регулярности не строится, пока ночей меньше `2 × MIN_OBSERVATIONS`.

## Связь с едой и сахаром

```
daily_kcal(intakes) / daily_carbs(intakes) -> {date: value}
daily_mean_glucose(points, tz) -> {date: mmol/L}
daily_mean_rise(meals, excursions, tz) -> {date: mmol/L}
contrast(values, group_a, group_b, metric, label_a, label_b) -> SleepContrast
build_report(nights, tz, intakes?, points?, meals?, excursions?) -> SleepReport
```

Четыре метрики × два разбиения (короткая/обычная ночь, сдвинутый/привычный
отбой). В отчёт попадают только `meaningful` контрасты: по ≥ 3 дня в каждой
группе. Сравниваются **сутки после ночи** — ночь ключуется датой пробуждения,
и значения берутся за ту же дату.

Формулировки — как везде: «в дни после коротких ночей съедено в среднем на N
ккал больше», никогда «недосып заставляет есть» (конституция, принцип II).

## Хранение (`src/db/repo.py`)

```
PRESENCE_MIN_GAP_MIN = 5
save_presence(session, user, at?) -> bool        # False, если предыдущая свежая
load_presence(session, user, since?) -> [datetime]
last_presence_at(session, user) -> datetime|None
load_sleep_intervals(session, user, since?) -> [SleepInterval]   # stage из payload
daily_intake(session, user, since?) -> [DayIntake]               # по локальным суткам
users_watching_presence(session) -> [User]
users_due_for_presence_reminder(session, now, silent_days=1, min_gap_days=3) -> [User]
mark_presence_reminder(session, user, at)
```

`presence_pings` уезжает в `/export` и стирается по `/delete` вместе со всем
остальным. Хранятся только отметки времени — ничего о содержании сообщений.

## Отметка появлений (`src/handlers/presence.py`)

`PresenceMiddleware` — outer-middleware на `dispatcher.update`: появление
засчитывается, даже если апдейт не подошёл ни одному обработчику. Два
предохранителя: троттлинг в процессе (`_seen`, не чаще одной записи в
`PRESENCE_MIN_GAP_MIN` минут на пользователя) и сама опция — у выключивших не
пишется ничего. `get_user`, а не `get_or_create_user`: незнакомому человеку
карточка не заводится ради факта его существования.

## Интерфейс (`src/handlers/sleep.py`)

`/sleep` — карточка: источник, число ночей, средняя длительность, обычный
отбой и подъём, доля коротких ночей, ровность режима, затем контрасты.
Кнопки `sl:how|on|off|health|menu`:

- `❓ Как это работает` — оба источника, честно про Bot API, что нужно от
  человека (не блокировать бота, заходить утром и вечером) и что хранится;
- `👀 Следить за сном` / `🚫 Выключить наблюдение` — переключатель
  `users.sleep_presence_enabled`; включение сразу ставит первую отметку, иначе
  напоминание сработает через сутки на пустой истории;
- `⌚️ Samsung Health` — переход к карточке `/health`.

То же текстом: `/set sleep on|off`. В `/stats` от сна остаётся одна строка
(`reporting.format_sleep_short`), в `/health` — упоминание, что сон с телефона
разбирается в `/sleep`.

## Напоминание (`src/scheduler.py`)

`run_presence_reminders(bot, now?)` — тик раз в час: у кого опция включена, а
последняя отметка старше `PRESENCE_SILENCE_DAYS`, тому одно письмо
(`reporting.SLEEP_PRESENCE_REMINDER`) — как отмечаться, как проверить
уведомления, как подключить Samsung Health и как выключить функцию. Не чаще
раза в `min_gap_days=3`, только в окно `QUIET_START..QUIET_END`. Метка
`users.last_presence_reminder_at` ставится **до** отправки: оборванная сеть не
должна дать второе письмо на следующем тике.

Молчащая функция хуже выключенной: человек думает, что сон считается, а ночей
нет.
