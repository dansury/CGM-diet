# features — рассказ о возможностях и скрытое меню

Не чаще раза в неделю — ровно одна возможность, которой пользователь ещё не
пользовался. Об одной возможности — не больше двух сообщений за всё время.
При `/start` подсказку не шлём: `repo.defer_hints` ставит `last_hint_at`, и
первый рассказ приходит не раньше чем через `HINT_PERIOD_DAYS`.

## Каталог (`src/features.py`)

```
Feature(key, title, blurb, command?, menu_button?, counter?)
FeatureState(status(new|shown|accepted|declined), shown, used)
FEATURES = plate, labs, workout, body, wellbeing, meds, check, dictionary,
           graph, stats, health, export        # порядок = приоритет рассказа
BY_KEY{key->Feature} ; BUTTON_FEATURE{кнопка меню->key}
MAX_HINTS=2 ; HINT_PERIOD_DAYS=7
```

```
is_used(feature, counts, state)->bool   # counter в repo.counts либо отметка used
hidden_keys(states)->{key}
pick_hint(counts, states)->Feature|None # пропускает used|accepted|declined|shown≥2
```

`counter` — ключ `repo.counts` для возможностей, оставляющих строки в БД. У
графика, статистики и выгрузки строк нет: там обращение помечается явно
(`repo.mark_feature_used`).

## Состояние (`feature_flags`, `users.last_hint_at`)

`status`: `new` → `shown` → `accepted` | `declined`. `declined` — окончательно:
из меню убрано, в рассылку не попадает. `users.last_hint_at` держит недельный
интервал одним значением на пользователя.

## Кнопки

```
feat:ok:<key>    «👍 Отлично»   -> accepted, ничего не скрываем
feat:no:<key>    «🚫 Не нужно»  -> declined, кнопка уходит из reply-меню и из
                                  меню команд Telegram (scope=chat)
feat:show:<key>  вернуть в меню (из /hidden) -> accepted
feat:close       закрыть список
```

## Обработчик (`src/handlers/features.py`)

```
maybe_send_hint(bot, chat_id)->key|None  # отметка ставится до отправки
menu_for(session, user)->ReplyKeyboardMarkup ; menu_of(chat_id)->ReplyKeyboardMarkup
mark_used(chat_id, key) ; sync_commands(bot, chat_id, hidden)
/hidden -> список скрытого + кнопки возврата
```

Всё fail-soft: подсказка, меню команд и отметка обращения не имеют права
уронить обработчик, ради которого пользователь пришёл.

## Расписание (`src/scheduler.py`)

`feature_hint_loop` — тот же часовой тик, что у напоминания о взвешивании.
`run_feature_hints(bot, now?)`: `repo.users_due_for_hint` (onboarded,
`last_hint_at` пуст или старше 7 дней) → тихие часы 9–20 локального времени →
`maybe_send_hint`. Возвращает число отправленных.

## Меню

`keyboards.main_menu(hidden?)` собирает reply-клавиатуру из `MENU_ROWS`,
пропуская кнопки скрытых возможностей. Скрытая возможность продолжает
работать: её команда принимается всегда, а `/hidden` возвращает её в меню.
