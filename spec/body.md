# body — тело, состав, цель, дневной коридор калорий

Профиль тела, замеры (вес + биоимпеданс), цель по весу и дневной ориентир по
калориям. Расчёты — `src/analytics/body.py` (чистый слой), тексты —
`src/reporting.py`, ввод — `src/handlers/body.py`, напоминания —
`src/scheduler.py`.

## Таблицы

```
body_profile   id user_id* height_cm birth_year sex(m|f|null) activity(sedentary|light|
               moderate|high|athlete) pregnant(bool|null) conditions(text|null)
               weight_prompt_days last_weight_prompt_at updated_at
               -- uq(user_id)
body_goals     id user_id kind(lose|maintain|gain) target_weight_kg start_weight_kg
               rate_kg_week target_kcal target_date started_at is_active
               -- индекс (user_id, is_active)
weights        + body_fat_pct muscle_mass_kg water_pct bone_mass_kg visceral_fat
                 bmr_kcal source(manual|text|voice|photo|scale)
```

Биоимпеданс необязателен: строка `weights` живёт и с одним `weight_kg`.
Графики состава рисуются, только если поля заполнены хотя бы дважды.

## Расчёты (`src/analytics/body.py`)

```
bmi(weight_kg, height_cm) -> float|None
bmi_category(bmi) -> str            # справочная формулировка ВОЗ, не диагноз
lean_mass(weight_kg, body_fat_pct) -> float|None
bmr(weight_kg, height_cm?, age?, sex?, body_fat_pct?) -> float|None
    # Кэтч-Макардл при известном проценте жира, иначе Миффлин–Сан-Жеор
tdee(bmr_kcal, activity) -> float
safe_rate_range(weight_kg, kind) -> (min_kg_week, max_kg_week)
build_plan(profile, weight_kg, goal_kind, target_weight_kg, rate_kg_week?,
    pregnant=False) -> EnergyPlan
day_balance(target_kcal, consumed_kcal, burned_kcal) -> DayBalance
weight_trend(series:[(at, kg)], window_days=14) -> WeightTrend|None
merge_burn(workouts, samples) -> float    # ккал за день без двойного счёта
```

`ACTIVITY_FACTORS`: sedentary 1.2, light 1.375, moderate 1.55, high 1.725,
athlete 1.9. `KCAL_PER_KG = 7700`.

`EnergyPlan`: `target_kcal, tdee_kcal, bmr_kcal, rate_kg_week, delta_kcal,
weeks, eta_date, capped:[str]`.

`DayBalance`: `target_kcal, consumed_kcal, burned_kcal, available_kcal,
share (0..~2), over:bool`.

`WeightTrend`: `first_at, last_at, first_kg, last_kg, rate_kg_week, to_goal_kg`.

### Границы диетологов (жёсткие, зашиты в `build_plan`)

| Ограничение | Значение | Что делает |
|---|---|---|
| темп снижения | 0.25–1.0 % массы тела в неделю, не более 1.0 кг | клип `rate_kg_week` |
| темп набора | 0.125–0.5 % массы тела в неделю, не более 0.5 кг | клип `rate_kg_week` |
| дефицит | не более 25 % от TDEE | клип `delta_kcal` |
| нижний порог калорий | 1500 (м) / 1200 (ж) / 1200 при неизвестном поле | клип `target_kcal` |
| ИМТ < 18.5 | цель на снижение не строится | `capped` + отказ |
| беременность (`pregnant=True`) | цель на снижение не строится вовсе, независимо от ИМТ | `PlanImpossible("pregnant")` |

Каждый сработавший клип попадает в `capped` и **обязан** быть показан
пользователю: тихо урезать чужую цель нельзя.

## Дневной коридор

`day_balance` считает: `available = target + burned − consumed`.
`share = consumed / (target + burned)`.

`day_progress_text(session, user, now)` — единая точка: после каждого
подтверждённого приёма пищи (`meal:ok`), после тренировки, после записи веса,
в `/body` и `/today`.

| Что есть | Что показываем |
|---|---|
| активная цель и `target_kcal` (из плана либо из цели) | `format_day_progress` — полоса, остаток, углеводы |
| цели нет или коридор не посчитался, но за день что-то съедено/потрачено | `format_day_totals` — итог дня и подсказка завести цель (`/body`) |
| за день ничего нет и цели нет | `None` |

Полоса из 10 клеток: `▓` съедено, `░` остаток, `▒` перебор.

Сбой расчёта коридора не имеет права съесть подтверждение записи: вызов в
`handlers/confirm.py` и `handlers/workout.py` fail-soft — исключение пишется в
лог, сообщение о записи уходит без полосы.

## Напоминание о взвешивании (`src/scheduler.py`)

`start_scheduler(bot)` поднимает `weight_reminder_loop(bot, interval_s=3600)` в
`prepare_runtime` (polling и webhook одинаково); повторный вызов не плодит
вторую задачу. Один тик — `run_weight_reminders(bot, now?)`:

1. `repo.users_due_for_weight(now)` — активная цель или заполненный профиль,
   последний замер старше `weight_prompt_days` (по умолчанию 14), последнее
   напоминание старше 3 дней;
2. локальное время пользователя в окне 09:00–20:00;
3. одно сообщение с кнопкой «⚖️ Ввести вес» (`bd:weight`),
   `last_weight_prompt_at` обновляется до отправки — сбой сети не даёт дублей.

Частота меняется командой `/set weighin <дни>` (3–90). Напоминание получает
только тот, у кого есть строка `body_profile`: непрошеное «встаньте на весы»
бот не присылает.

Ввести вес можно и самому в любой момент: «вес 72,3», `/weight`, кнопка в
`/body`, голосом и фото весов.

## Ввод

- текст/голос: `ingest/text_parse.py` достаёт `вес`, `рост`, `возраст`, `пол`,
  `жир %`, `мышцы`, `вода %`, `кости`, `висцеральный`, `цель <кг>`;
- фото весов/распечатки биоимпеданса: `recognize.recognize_body_photo`
  (`TASK: body_scale`), карточка → `bd:save`;
- `/body` — карточка профиля и цели, поля правятся кнопками
  (`BodyFlow.awaiting` + ключ `body_field`);
- беременность и особые состояния/заболевания задаются один раз при первом
  запуске (`spec/onboarding.md`) и правятся позже кнопками `/body`:
  `bd:field:pregnant` (кнопка видна только при `sex == "f"`) и
  `bd:field:conditions` (свободный текст, `body.normalize_conditions`
  сворачивает «нет»/пусто в `None` — не хранить отсутствие состояния как текст).

## Клавиатуры и callback

```
bd:menu|weight|goal|chart|close
bd:field:<height|age|sex|activity|weight|goal|pregnant|conditions>
bd:sex:<m|f>          bd:act:<sedentary|light|moderate|high|athlete>
bd:preg:<y|n>
bd:rate:<кг/нед ×100, целое>       # 25 → 0.25 кг/нед; целевой вес лежит в FSM
bd:save                            # карточка замера с фото; отмена — общий x:cancel
```

## Тексты (`src/reporting.py`)

```
format_body_card(profile?, last?, goal?, plan?, trend?, bmi_value?, bmi_note?, age?) -> str
format_day_progress(balance, *, goal?, trend?) -> str
format_day_totals(balance) -> str
format_measurement_draft(draft) -> str
format_goal_plan(plan, *, kind, target_weight_kg) -> str
format_weight_saved(weight, previous?, goal?) -> str
progress_bar(share, width=10) -> str
WEIGHT_PROMPT ; BODY_DISCLAIMER
```

Клинические границы (`spec/clinical.md`): ориентир по калориям — рекомендация
по питанию, она допустима и всегда сопровождается: (1) числом, из которого
получена (BMR/TDEE и формула), (2) напоминанием, что при беременности,
заболеваниях почек, диабете и приёме лекарств коридор согласуют с врачом.
Категория ИМТ подаётся как справка ВОЗ, без слова «диагноз» и без «у вас».
