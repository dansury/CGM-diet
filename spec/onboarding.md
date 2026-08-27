# onboarding — анкета при первом запуске

Один раз, сразу после `WELCOME` в `common.cmd_start`, для `user.onboarded ==
False` на входе (флаг проверяется **до** того, как `cmd_start` ставит его в
`True`). Сбой анкеты не должен ронять `/start` — обёрнут в `try/except`.
Первое, что видит новый пользователь после приветствия, — вопрос о целях;
рассказ о неиспользованных возможностях при первом `/start` не отправляется
вовсе (`spec/features.md`).
Маршрутизация — `src/handlers/onboarding.py`, поля пишутся туда же, куда и
`/body` (`spec/body.md`): числа и цель проходят через существующие функции
`body.py`, чтобы диетологические ограничители не дублировались.

## FSM

```
OnboardingFlow.asking          # текущий шаг — `onb_step`, очередь — `onb_queue`
GoalsFlow.note                 # «Свой вариант» цели — свободный текст
STEPS = focus, age, height, weight, sex, conditions, goal
```

`pregnant` — не в статическом списке: вставляется в начало очереди из
`on_sex`, только если выбран пол `f`. Каждый шаг можно пропустить
(`onb:skip`) — анкета не блокирует ничего, версия «можно позже» верна для
каждого поля, включая биоимпеданс, который в очередь вообще не входит:
про него только напоминание в `INTRO`.

## Callback-грамматика

```
onb:skip              # пропустить текущий шаг
onb:sex:<m|f>
onb:preg:<y|n>
gl:pick:<key>         # цель отмечена/снята (множественный выбор)
gl:other              # «Свой вариант» -> GoalsFlow.note
gl:done               # сохранить выбор и идти дальше
```

## Прерывание анкеты

`_should_handle(message, state)` — фильтр обработчика `on_answer`
(`OnboardingFlow.asking`), решает, что ответ анкеты, а что нет:

- активный `pending_mode` (`intake.MODE_KEY`) — пропускает сообщение дальше,
  в `intake`: пользователь уже нажал «🍽 Записать еду» и т.п.;
- явная команда (`/…`) или текст кнопки reply-меню — тоже пропускает дальше;
- иначе (свободный текст) — ответ на текущий вопрос;
- **фото** — всегда перехватывается здесь (кроме случая с `pending_mode`),
  даже если ждали текст.

Явные команды и кнопки меню обрабатываются своими хендлерами (`common`,
`body`, `intake`) обычным образом; большинство из них сами делают
`state.clear()`, чем и завершают анкету. Оставшиеся вопросы просто не
задаются — данные дополняются позже через `/body`.

**Фото вместо ответа** (`_handle_photo`): анкета завершается
(`state.clear()`), бот один раз напоминает «профиль можно закончить в любой
момент — /body», затем фото идёт по обычному пути —
`recognize.classify_photo` → `intake._dispatch` (еда, весы, анализы, этикетка,
лекарство, тренировка или «не понял, что на фото»). Голос во время анкеты
пока не маршрутизируется отдельно (нет записи в `intake._route_voice`) —
попадает в обычный `parse_text`/`parse_meal_text` конвейер.

## Шаги

| Шаг | Ввод | Куда пишется |
|---|---|---|
| `focus` | кнопки `gl:pick:*` + `gl:done`; свободный текст = свой вариант | `body_profile.focus` (ключи через запятую), `focus_note` |
| `age` | число 10–120 | `body_profile.birth_year` |
| `height` | число 100–250 | `body_profile.height_cm` |
| `weight` | число 25–400 или фото весов | `body.save_weight_entry` (та же запись, что и `/weight`) |
| `sex` | кнопки `onb:sex:<m|f>` | `body_profile.sex`; `f` → доп. вопрос `pregnant` |
| `pregnant` | кнопки `onb:preg:<y|n>` | `body_profile.pregnant` |
| `conditions` | свободный текст | `body_profile.conditions` (`body.normalize_conditions`: «нет»/пусто → `None`) |
| `goal` | число (целевой вес) | `body.offer_goal` — та же цепочка `bd:rate:*` / `_save_goal`, что и в `/body`; сама завершает анкету |

`goal` в очереди остаётся, только если `goals.wants_weight_goal(focus)` —
целевой вес спрашиваем у того, кто пришёл менять вес (`weight`, `muscle`) или
не назвал целей вовсе. Иначе шаг снимается в `after_focus`, и `_finish` говорит
об этом (`FINISH_NO_WEIGHT_GOAL`).

`goal` — последний шаг: очередь после него пуста, `_finish` уже не
понадобится (`offer_goal`/`on_rate` сами чистят состояние). Если пользователь
пропускает `goal` кнопкой — очередь пустеет, вызывается `_finish`.

## Клавиатуры (`src/keyboards.py`)

```
onboarding_skip()               # «⏭ Пропустить» + общий крестик
onboarding_sex_picker()         # мужской/женский + пропустить + крестик
onboarding_pregnancy_picker()   # да/нет + крестик
focus_picker(selected, skippable?)  # цели: ☑️/▫️ + «Свой вариант» + «Готово»
                                    # + «Пропустить» (анкета) либо крестик (/body)
```

## Клиническая граница

`beременность` учитывается в `analytics.body.build_plan(pregnant=…)`:
цель `kind="lose"` не строится вовсе (`PlanImpossible("pregnant")`),
независимо от ИМТ — дефицит калорий при беременности не предлагается ни при
каких вводных (`spec/clinical.md`, принцип I). Сообщение об этом — в
`handlers/body._save_goal`, рядом с существующей веткой ИМТ < 18.5.

## Цели (`src/goals.py`, `src/handlers/goals.py`)

Первый шаг анкеты и кнопка «🎯 Мои цели» в `/body` (`bd:field:focus`) — один и
тот же список; после «Готово» онбординг идёт дальше, `/body` просто
подтверждает.

```
Goal(key, title, features:[ключи src/features.py])
GOALS = weight, sugar, energy, habits, symptoms, labs, muscle, sport
CUSTOM = "custom" ; NOTE_LIMIT = 200 ; WEIGHT_GOALS = {weight, muscle}
decode(raw)->(key,…)          # порядок каталога, чужие ключи отброшены
encode(keys)->str             # "" = спросили, целей не назвал; NULL = не спрашивали
titles(keys, note?)->[str]    # свой вариант — как написан
wants_weight_goal(keys)->bool ; feature_order(keys)->(feature_key,…)
normalize_note(text)->str|None
```

Хранение — `body_profile.focus` (ключи через запятую) и `focus_note`
(свой вариант дословно, не разбирается). Выбор до нажатия «Готово» живёт в FSM
(`focus_sel`), не в callback-data.

Что цели меняют:
- очередь анкеты — целевой вес спрашивается не у всех (см. выше);
- порядок подсказок о возможностях — `features.pick_hint(priority=…)`
  (`spec/features.md`);
- строку «Цели: …» в карточке `/body` (`reporting._focus_line`).

Список целей — не диагноз и не сегментация здоровья: он влияет только на
порядок сказанного, никогда — на клинические границы (`spec/clinical.md`).
