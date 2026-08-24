# workout — тренировки, ходьба, энергозатраты

Ввод тренировки текстом, голосом и фото (в том числе рукописного дневника),
дополнительные вопросы и оценка энергозатрат по MET. Расчёт —
`src/analytics/workout.py`, ввод и вопросы — `src/handlers/workout.py`,
распознавание — `src/vision/recognize.py`, тексты — `src/reporting.py`.

## Таблица

```
workouts   id user_id started_at ended_at? kind slug title duration_min
           intensity(low|moderate|high) distance_m steps avg_hr rpe sweat(yes|no|light)
           kcal kcal_source(estimated|user|device) met source(text|voice|photo|manual)
           media_id note
           -- индекс (user_id, started_at)
```

Ручная тренировка не пишется в `activity_samples`: там живёт то, что прислал
телефон. При подсчёте сожжённого за день `analytics.body.merge_burn`
выбрасывает присланные телефоном тренировки, пересекающиеся по времени с
ручной записью, — иначе одна пробежка посчиталась бы дважды.

## MET (`src/analytics/workout.py`)

```
KINDS: slug -> (русская метка, {low, moderate, high} -> MET)
```

Закрытый словарь из 20 видов: `walking, running, cycling, swimming, strength,
hiit, elliptical, rowing, yoga, stretching, dance, football, basketball, tennis,
boxing, skiing, skating, stairs, housework, other`.

```
resolve_kind(text) -> slug
resolve_intensity(stated?, rpe?, sweat?, avg_hr?, age?) -> (intensity, basis)
looks_like_workout(text) -> bool ; parse_duration(text) -> minutes?
met_for(kind, intensity, kmh?) -> float
kcal_estimate(kind, intensity, minutes, weight_kg?, *, kmh?, avg_hr?, age?) -> Estimate
kcal_from_steps(steps, weight_kg?, minutes?) -> float
missing_questions(draft) -> [str]      # duration → intensity → sweat
```

`Estimate`: `kcal, met, weight_kg, minutes, assumed_weight:bool, basis:str`.
Формула: `kcal = MET · 3.5 · вес(кг) / 200 · минуты`.
Скорость (когда известны расстояние и время) выбирает MET внутри вида —
бег 8 км/ч и 14 км/ч это разные MET.
Пульс сильнее субъективной интенсивности: доля от `220 − возраст`
(< 64 % → low, < 77 % → moderate, иначе high).
Пот — самый слабый признак и используется, только если интенсивность не
названа и пульса нет.
Вес неизвестен → берётся 70 кг, `assumed_weight=True`, и в тексте стоит «≈».

## Поток

1. текст/голос («бегал 40 минут», «10000 шагов», «час на велике») →
   `recognize.parse_workout_text` (`TASK: workout_text`);
   фото трекера, часов или бумажного дневника →
   `recognize.recognize_workout_photo` (`TASK: workout_photo`, читает и почерк);
2. `missing_questions` → бот задаёт недостающее кнопками:
   длительность (`wo:dur:<мин>`), интенсивность (`wo:int:<low|moderate|high>`),
   «вспотели?» (`wo:sweat:<yes|light|no>`);
3. карточка с оценкой энергозатрат → `wo:ok` пишет строку, `wo:hr` спрашивает
   пульс, `wo:edit` — правка текстом или голосом, `wo:time` — другое время;
4. записанные ккал попадают в дневной коридор (`spec/body.md`) и в `/today`.

FSM: `WorkoutFlow.confirming|asking|editing|retiming|awaiting_hr`.
Ключи FSM: `draft` (общий), `wo_pending` — очередь вопросов.

## Клавиатуры

```
wo:ok|edit|time|hr|drop
wo:dur:<15|30|45|60|90|other>
wo:int:<low|moderate|high>
wo:sweat:<yes|light|no>
```

Кнопка меню — `🏃 Тренировка`, команды `/workout` (ввод) и `/workouts`
(журнал за неделю). Свободный текст вида «бегал 40 минут» распознаётся и без
команды: `analytics.workout.looks_like_workout` требует и слово про движение,
и число, поэтому «салат 200 г» тренировкой не станет.

## Тексты

```
format_workout_draft(draft, *, estimate?, started_at?, applied?) -> str
format_workouts(rows, days=7) -> str
```

Энергозатраты всегда подаются как оценка: «≈ 320 ккал (MET 7.0, 40 мин)».
Точное число обещать нельзя — это модель расхода, а не измерение.
