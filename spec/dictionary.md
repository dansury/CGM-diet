# dictionary — личный словарь и подсказки по первым буквам

Цель: то, что человек ест и принимает регулярно, вводится **одним нажатием**,
а не фотографией и подтверждением каждый раз.

## Что попадает в словарь

| kind | Когда | Порог показа |
|---|---|---|
| `meal` | при `meal:ok` — заголовок приёма | со **второго** раза (`hits >= 2`) |
| `item` | при `meal:ok` — каждое блюдо состава | со второго раза |
| `medication` | при `med:ok` и при разборе текста | **сразу** (`hits >= 1`) |

Порог зашит в `MIN_HITS = {"meal": 2, "item": 2, "medication": 1}`
(`src/db/repo.py`). Требование владельца: «блюда, которые встретились более
одного раза».

## Таблица

```
user_dictionary  id user_id kind(meal|item|medication) key_norm label
                 payload(JSON) hits pinned is_active last_used_at created_at
                 -- uq(user_id, kind, key_norm) ; ix(user_id, kind, key_norm)
```

`payload` — то, чем восстанавливают запись без модели: для еды
`meal_to_dict(draft)`, для лекарства `{"dose_text","inn","slug","cid"}`.
Удаление — `is_active=False` (запись перестаёт предлагаться и не воскресает
сама; `hits` сохраняется, чтобы повторное подтверждение не считалось «первым»).

## Репозиторий (`src/db/repo.py`)

```
bump_dictionary(session, user, *, kind, label, payload=None, hits=1) -> DictEntry
suggest_dictionary(session, user, prefix, *, kinds=None, limit=6) -> list[DictEntry]
  # 1) префикс по key_norm  2) вхождение подстроки  3) сортировка:
  #    pinned desc, hits desc, last_used_at desc; неактивные исключены
list_dictionary(session, user, *, kind=None, limit=30, include_hidden=False)
get_dictionary_entry(session, user, entry_id) -> DictEntry | None
touch_dictionary(session, entry) -> None            # hits+1, last_used_at=now
hide_dictionary(session, entry) -> None             # is_active=False
```

## UX (`src/handlers/dictionary.py`)

**Порядок** — подсказка всегда впереди модели:

1. пользователь пишет текст → детерминированный `parse_text` (сахар/вес/лекарства);
2. остаток ≥ 2 символов → `suggest_dictionary(prefix=остаток)`;
3. есть совпадения → карточка «Может быть, это?» с inline-клавиатурой
   (до 6 кнопок, по одной в ряд) + `➕ Разобрать как новое`;
4. нажатие `dict:use:<id>` → еда: карточка подтверждения из `payload`
   (модель не зовётся вовсе); лекарство: запись приёма «сейчас», сразу;
5. совпадений нет или нажали «новое» → как раньше, `recognize.parse_meal_text`.

`/my` (и кнопка `⭐️ Мой словарь`) — разделы 🍽 / 💊, до 12 кнопок,
`🗑 Удалить` переключает клавиатуру в режим удаления.

Callback-грамматика:
```
dict:use:<id> | dict:new | dict:page:<kind>:<n>
dict:mode:<kind>:<use|del> | dict:rm:<id> | dict:close
```

Кнопки словаря — единственный путь записи «в один тап»; всё остальное
по-прежнему проходит подтверждение (конституция, IV: подтверждение уже дано
в момент, когда запись попала в словарь).
