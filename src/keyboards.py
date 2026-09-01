"""Inline keyboards and callback-data grammar.

Callback data is `<domain>:<action>[:<arg>]`, kept under Telegram's 64-byte
limit. Draft payloads never travel in callback data — they live in the FSM
state, so a stale button can only act on data the user still owns.
See `spec/bot.md` § Keyboards.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from src.analytics.tags import tag_label

CANCEL_DATA = "x:cancel"
CANCEL_TEXT = "❌ Отменить"


def cancel_button() -> InlineKeyboardButton:
    """Единый крестик: отменяет любой ввод, на какой бы стадии он ни был.

    Одна и та же кнопка на всех карточках и во всех подсказках — человек не
    должен вспоминать, как называется отмена именно здесь.
    """
    return InlineKeyboardButton(text=CANCEL_TEXT, callback_data=CANCEL_DATA)


def cancel_only() -> InlineKeyboardMarkup:
    """Клавиатура для приглашений «напишите текстом» — только отмена."""
    return InlineKeyboardMarkup(inline_keyboard=[[cancel_button()]])


MENU_ROWS: tuple[tuple[str, ...], ...] = (
    ("🍽 Записать еду", "🩸 Записать сахар"),
    ("🛒 Проверить продукт", "🙂 Самочувствие"),
    ("🏃 Тренировка", "⚖️ Вес и цель"),
    ("📊 Статистика", "📈 График"),
    ("⭐️ Мой словарь", "💊 Лекарства"),
)


def main_menu(hidden: set[str] | None = None) -> ReplyKeyboardMarkup:
    """Меню без того, от чего пользователь отказался (`spec/features.md`).

    Скрытая кнопка не отключает возможность: команда работает, а вернуть её в
    меню можно из `/hidden`.
    """
    from src.features import BUTTON_FEATURE

    skip = hidden or set()
    keyboard = []
    for row in MENU_ROWS:
        buttons = [
            KeyboardButton(text=text)
            for text in row
            if BUTTON_FEATURE.get(text) not in skip
        ]
        if buttons:
            keyboard.append(buttons)
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Пришлите фото, текст или голосовое",
    )


def feature_hint(key: str) -> InlineKeyboardMarkup:
    """Две кнопки под рассказом о возможности — и третьей быть не должно."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍 Отлично", callback_data=f"feat:ok:{key}"),
                InlineKeyboardButton(text="🚫 Не нужно", callback_data=f"feat:no:{key}"),
            ]
        ]
    )


def hidden_features(features) -> InlineKeyboardMarkup:
    """`/hidden`: вернуть скрытую возможность в меню одной кнопкой."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"↩️ {feature.title}", callback_data=f"feat:show:{feature.key}"
            )
        ]
        for feature in features
    ]
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data="feat:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_meal() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="meal:ok"),
                InlineKeyboardButton(text="✏️ Скорректировать", callback_data="meal:edit"),
            ],
            [
                InlineKeyboardButton(text="✏️ БЖУ", callback_data="meal:macros"),
                InlineKeyboardButton(text="🕒 Другое время", callback_data="meal:time"),
            ],
            [cancel_button()],
            [InlineKeyboardButton(text="🤖 Это не еда", callback_data="photo:reroute")],
        ]
    )


def confirm_glucose() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="glu:ok"),
                InlineKeyboardButton(text="✏️ Скорректировать", callback_data="glu:edit"),
            ],
            [
                InlineKeyboardButton(text="🔁 Сменить единицы", callback_data="glu:unit"),
                cancel_button(),
            ],
        ]
    )


def confirm_labs() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="lab:ok"),
                InlineKeyboardButton(text="✏️ Скорректировать", callback_data="lab:edit"),
            ],
            [cancel_button()],
        ]
    )


def confirm_medication() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="med:ok"),
                InlineKeyboardButton(text="✏️ Скорректировать", callback_data="med:edit"),
            ],
            [
                InlineKeyboardButton(text="🕒 Другое время", callback_data="med:time"),
                cancel_button(),
            ],
            [InlineKeyboardButton(text="🤖 Это не лекарство", callback_data="photo:reroute")],
        ]
    )


def product_actions(*, mode: str) -> InlineKeyboardMarkup:
    """`check` — перед покупкой; `eaten` — уже съедено."""
    rows: list[list[InlineKeyboardButton]] = []
    if mode == "check":
        rows.append(
            [InlineKeyboardButton(text="🍽 Я это съел(а)", callback_data="prod:eat")]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="✅ Записать как еду", callback_data="prod:eat")]
        )
    # Отдельного шага «вторая сторона» нет: обе стороны присылаются одним
    # альбомом и уходят в модель одним вызовом (`spec/bot.md` § Потоки).
    rows.append([InlineKeyboardButton(text="💾 Только запомнить", callback_data="prod:save")])
    rows.append(
        [
            InlineKeyboardButton(text="✏️ БЖУ", callback_data="prod:macros"),
            InlineKeyboardButton(text="✏️ Скорректировать", callback_data="prod:edit"),
        ]
    )
    rows.append([cancel_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def health_setup(*, step: str = "menu") -> InlineKeyboardMarkup:
    """Инструкция Samsung Health: шаги листаются кнопками (`spec/health_sync.md`)."""
    rows: list[list[InlineKeyboardButton]] = []
    if step != "how":
        rows.append([InlineKeyboardButton(text="📲 Как подключить", callback_data="hs:how")])
    if step != "keys":
        rows.append([InlineKeyboardButton(text="🔑 Мои ключи", callback_data="hs:keys")])
    if step != "app":
        rows.append([InlineKeyboardButton(text="📦 Приложение-мост", callback_data="hs:app")])
    if step != "menu":
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="hs:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sleep_setup(*, step: str = "menu", presence_on: bool = False) -> InlineKeyboardMarkup:
    """Карточка сна: инструкция и переключатель наблюдения (`spec/sleep.md`)."""
    rows: list[list[InlineKeyboardButton]] = []
    if step != "how":
        rows.append([InlineKeyboardButton(text="❓ Как это работает", callback_data="sl:how")])
    if presence_on:
        rows.append(
            [InlineKeyboardButton(text="🚫 Выключить наблюдение", callback_data="sl:off")]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="👀 Следить за сном", callback_data="sl:on")]
        )
    rows.append([InlineKeyboardButton(text="⌚️ Samsung Health", callback_data="sl:health")])
    if step != "menu":
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="sl:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


KIND_ICONS = {
    "meal": "🍽",
    "item": "🥄",
    "product": "🛒",
    "medication": "💊",
    "symptom": "🙂",
}

#: разделы словаря в том порядке, в каком они листаются в `/my`.
KIND_TABS: tuple[tuple[str, str], ...] = (
    ("meal", "🍽 Блюда"),
    ("item", "🥄 Продукты"),
    ("product", "🛒 Упаковки"),
    ("medication", "💊 Лекарства"),
    ("symptom", "🙂 Самочувствие"),
)


def dictionary_suggestions(entries: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    """Prediction by the first characters: one tap instead of a fresh recognition.

    `entries` are (id, kind, label); the id is all that travels in callback data
    (Telegram's 64-byte limit), the payload stays in the database.
    """
    rows = [
        [
            InlineKeyboardButton(
                text=f"{KIND_ICONS.get(kind, '•')} {label}"[:64],
                callback_data=f"dict:use:{entry_id}",
            )
        ]
        for entry_id, kind, label in entries
    ]
    rows.append(
        [
            InlineKeyboardButton(text="➕ Разобрать как новое", callback_data="dict:new"),
            cancel_button(),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


#: сколько кнопок «в словарь» вешаем под записанным приёмом пищи
PIN_BUTTONS_LIMIT = 6
#: длина названия на такой кнопке — дальше обрезаем
PIN_LABEL_LIMIT = 32


def dictionary_pins(
    entries: list[tuple[int, str, str]], *, glucose: bool = False
) -> InlineKeyboardMarkup | None:
    """«⭐️ в словарь» на каждую позицию только что записанного приёма пищи.

    `entries` — (id, kind, label). Ждать второго раза необязательно: человек
    сам говорит, что хочет повторять это одной кнопкой
    (`spec/dictionary.md` § Запись в словарь одной кнопкой).
    """
    rows = [
        [
            InlineKeyboardButton(
                text=f"⭐️ {label[:PIN_LABEL_LIMIT]} → в словарь"[:64],
                callback_data=f"dict:pin:{entry_id}",
            )
        ]
        for entry_id, _kind, label in entries[:PIN_BUTTONS_LIMIT]
    ]
    # Предложение прислать замер живёт на той же карточке, а не отдельным
    # сообщением (`spec/onboarding.md` § Сахарный трек).
    if glucose:
        rows.append([glucose_log_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def dictionary_page(
    entries: list[tuple[int, str, str]], *, kind: str, mode: str = "use", offset: int = 0
) -> InlineKeyboardMarkup:
    """`mode`: `use` — записать одним нажатием, `del` — убрать из словаря."""
    prefix = "dict:rm" if mode == "del" else "dict:use"
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'🗑 ' if mode == 'del' else ''}{KIND_ICONS.get(k, '•')} {label}"[:64],
                callback_data=f"{prefix}:{entry_id}",
            )
        ]
        for entry_id, k, label in entries
    ]
    tabs = [
        InlineKeyboardButton(
            text=title if tab != kind else f"• {title}",
            callback_data=f"dict:page:{tab}:0",
        )
        for tab, title in KIND_TABS
    ]
    for start in range(0, len(tabs), 2):
        rows.append(tabs[start : start + 2])
    rows.append(
        [
            InlineKeyboardButton(
                text="✍️ Записать" if mode == "del" else "🗑 Удалить",
                callback_data=f"dict:mode:{kind}:{'use' if mode == 'del' else 'del'}",
            ),
            InlineKeyboardButton(text="✖️ Закрыть", callback_data="dict:close"),
        ]
    )
    if offset:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data=f"dict:page:{kind}:{max(offset - 12, 0)}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def photo_kind() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🍽 Еда", callback_data="kind:food"),
                InlineKeyboardButton(text="🩸 Сахар", callback_data="kind:glucose_screen"),
            ],
            [
                InlineKeyboardButton(text="🏷 Этикетка", callback_data="kind:food_label"),
                InlineKeyboardButton(text="🧪 Анализы", callback_data="kind:lab_report"),
            ],
            [
                InlineKeyboardButton(text="💊 Лекарство", callback_data="kind:medication"),
                InlineKeyboardButton(text="⚖️ Весы", callback_data="kind:body_scale"),
            ],
            [InlineKeyboardButton(text="🏃 Тренировка", callback_data="kind:workout")],
            [cancel_button()],
        ]
    )


def wellbeing_score() -> InlineKeyboardMarkup:
    labels = {5: "5 отлично", 4: "4 хорошо", 3: "3 так себе", 2: "2 плохо", 1: "1 очень плохо"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=labels[5], callback_data="wb:score:5")],
            [InlineKeyboardButton(text=labels[4], callback_data="wb:score:4")],
            [InlineKeyboardButton(text=labels[3], callback_data="wb:score:3")],
            [InlineKeyboardButton(text=labels[2], callback_data="wb:score:2")],
            [InlineKeyboardButton(text=labels[1], callback_data="wb:score:1")],
            [cancel_button()],
        ]
    )


def symptom_picker(
    symptoms: list[tuple[int, str]], selected: set[int]
) -> InlineKeyboardMarkup:
    """Two-per-row toggles from the user's own glossary, plus free input."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for symptom_id, label in symptoms:
        mark = "☑️ " if symptom_id in selected else ""
        row.append(
            InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"wb:sym:{symptom_id}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(text="➕ Другое", callback_data="wb:other"),
            InlineKeyboardButton(text="🎤 Голосом", callback_data="wb:voice"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="✅ Готово", callback_data="wb:done"),
            cancel_button(),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plate_settings(*, enabled: bool) -> InlineKeyboardMarkup:
    """Buttons under /plate: toggle and meals-per-day."""
    toggle = (
        InlineKeyboardButton(text="Выключить", callback_data="plt:off")
        if enabled
        else InlineKeyboardButton(text="Включить", callback_data="plt:on")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [toggle],
            [InlineKeyboardButton(text="Количество приёмов пищи", callback_data="plt:meals")],
        ]
    )


def plate_meals_picker(*, current: int | None) -> InlineKeyboardMarkup:
    """Sub-menu: edit meals count or switch to auto."""
    mark_edit = f"✏️ {current}" if current else "✏️ 3"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=mark_edit, callback_data="plt:medit"),
                InlineKeyboardButton(text="✨ Автоматически", callback_data="plt:mauto"),
            ],
        ]
    )


def stats_windows(active: str = "1h") -> InlineKeyboardMarkup:
    def mark(window: str, text: str) -> str:
        return f"• {text}" if window == active else text

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=mark("1h", "Через 1 ч"), callback_data="stats:w:1h"),
                InlineKeyboardButton(text=mark("2h", "Через 2 ч"), callback_data="stats:w:2h"),
            ],
            [
                InlineKeyboardButton(text="🏷 По компонентам", callback_data="stats:k:tag"),
                InlineKeyboardButton(text="🍲 По блюдам", callback_data="stats:k:item"),
            ],
            [InlineKeyboardButton(text="📈 График", callback_data="stats:chart")],
        ]
    )


# ------------------------------------------------------------------ тело и цель

def body_menu(*, has_goal: bool, show_pregnancy: bool = False) -> InlineKeyboardMarkup:
    """Карточка `/body`: замер, профиль, цель, график."""
    rows = [
        [
            InlineKeyboardButton(text="⚖️ Записать вес", callback_data="bd:weight"),
            InlineKeyboardButton(
                text="🎯 Изменить цель" if has_goal else "🎯 Задать цель",
                callback_data="bd:goal",
            ),
        ],
        [
            InlineKeyboardButton(text="📏 Рост", callback_data="bd:field:height"),
            InlineKeyboardButton(text="🎂 Возраст", callback_data="bd:field:age"),
        ],
        [
            InlineKeyboardButton(text="⚧ Пол", callback_data="bd:field:sex"),
            InlineKeyboardButton(text="🏃 Активность", callback_data="bd:field:activity"),
        ],
    ]
    rows.append([InlineKeyboardButton(text="🎯 Мои цели", callback_data="bd:field:focus")])
    second_row = [InlineKeyboardButton(text="🩺 Особые состояния", callback_data="bd:field:conditions")]
    if show_pregnancy:
        second_row.append(InlineKeyboardButton(text="🤰 Беременность", callback_data="bd:field:pregnant"))
    rows.append(second_row)
    rows.append([InlineKeyboardButton(text="📉 График веса", callback_data="bd:chart")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pregnancy_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="bd:preg:y"),
                InlineKeyboardButton(text="Нет", callback_data="bd:preg:n"),
            ],
            [cancel_button()],
        ]
    )


def sex_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мужской", callback_data="bd:sex:m"),
                InlineKeyboardButton(text="Женский", callback_data="bd:sex:f"),
            ],
            [cancel_button()],
        ]
    )


def activity_picker() -> InlineKeyboardMarkup:
    """Уровни как их описывают диетологи — человек выбирает словами, не числом."""
    from src.analytics.body import ACTIVITY_LABELS

    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"bd:act:{slug}")]
        for slug, label in ACTIVITY_LABELS.items()
    ]
    rows.append([cancel_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rate_picker(options: list[float], *, recommended: float | None = None) -> InlineKeyboardMarkup:
    """Только темпы внутри безопасных рамок (`spec/body.md`)."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for value in options:
        mark = " ⭐️" if recommended is not None and abs(value - recommended) < 1e-6 else ""
        row.append(
            InlineKeyboardButton(
                text=f"{value:g} кг/нед{mark}", callback_data=f"bd:rate:{int(round(value * 100))}"
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([cancel_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_measurement() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Записать", callback_data="bd:save"),
                InlineKeyboardButton(text="✏️ Скорректировать", callback_data="bd:field:weight"),
            ],
            [cancel_button()],
        ]
    )


# ------------------------------------------------------------------ цели

def focus_picker(selected, *, skippable: bool = False) -> InlineKeyboardMarkup:
    """Множественный выбор целей: одно нажатие — одна цель, «Готово» — конец.

    Выбор живёт в FSM, а не в callback-data: кнопка, нажатая через сутки, не
    может подменить чужой набор целей (`spec/onboarding.md` § Цели).
    """
    from src.goals import CUSTOM, GOALS

    picked = set(selected or ())
    rows = [
        [
            InlineKeyboardButton(
                text=("☑️ " if goal.key in picked else "▫️ ") + goal.title,
                callback_data=f"gl:pick:{goal.key}",
            )
        ]
        for goal in GOALS
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=("☑️ Свой вариант" if CUSTOM in picked else "✍️ Свой вариант"),
                callback_data="gl:other",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="gl:done")])
    rows.append(
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="onb:skip")]
        if skippable
        else [cancel_button()]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ------------------------------------------------------------------ онбординг

def onboarding_skip() -> InlineKeyboardMarkup:
    """Любой шаг анкеты можно пропустить и заполнить позже через /body."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="onb:skip")],
            [cancel_button()],
        ]
    )


def onboarding_sex_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мужской", callback_data="onb:sex:m"),
                InlineKeyboardButton(text="Женский", callback_data="onb:sex:f"),
            ],
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="onb:skip")],
            [cancel_button()],
        ]
    )


def onboarding_pregnancy_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="onb:preg:y"),
                InlineKeyboardButton(text="Нет", callback_data="onb:preg:n"),
            ],
            [cancel_button()],
        ]
    )


def onboarding_meals_picker() -> InlineKeyboardMarkup:
    """Сколько раз в день человек обычно ест — сид для приёмов пищи (`spec/plate.md`)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="2 раза", callback_data="onb:meals:2"),
                InlineKeyboardButton(text="3 раза", callback_data="onb:meals:3"),
                InlineKeyboardButton(text="4 раза", callback_data="onb:meals:4"),
                InlineKeyboardButton(text="5 раз", callback_data="onb:meals:5"),
            ],
            [InlineKeyboardButton(text="⏭ Не знаю — посчитай сам", callback_data="onb:skip")],
            [cancel_button()],
        ]
    )


def diabetes_picker() -> InlineKeyboardMarkup:
    """Один выбор из каталога `src/sugar.py`; вопрос можно пропустить."""
    from src.sugar import DIABETES

    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"sg:dia:{key}")]
        for key, title in DIABETES
    ]
    rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data="onb:skip")])
    rows.append([cancel_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def glucose_method_picker(selected, *, skippable: bool = False) -> InlineKeyboardMarkup:
    """Чем человек меряет сахар: множественный выбор, «никакими» — исключающий.

    Как и в списке целей, выбор живёт в FSM, а не в callback-data
    (`spec/onboarding.md` § Сахарный трек).
    """
    from src.sugar import METHODS, NONE

    picked = set(selected or ())
    rows = [
        [
            InlineKeyboardButton(
                text=("☑️ " if key in picked else "▫️ ") + title,
                callback_data=f"sg:m:{key}",
            )
        ]
        for key, title in METHODS
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=("☑️ " if NONE in picked else "▫️ ") + "Никакими",
                callback_data=f"sg:m:{NONE}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="sg:done")])
    rows.append(
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="onb:skip")]
        if skippable
        else [cancel_button()]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def glucose_log_button() -> InlineKeyboardButton:
    """«🩸 Записать сахар» — та же кнопка под записью еды и в напоминании."""
    return InlineKeyboardButton(text="🩸 Записать сахар", callback_data="sg:log")


def weight_prompt() -> InlineKeyboardMarkup:
    """Кнопка из напоминания: попасть в ввод веса одним нажатием."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚖️ Ввести вес", callback_data="bd:weight")],
            [InlineKeyboardButton(text="Не сейчас", callback_data="bd:close")],
        ]
    )


# ------------------------------------------------------------------ тренировки

def confirm_workout() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="wo:ok"),
                InlineKeyboardButton(text="✏️ Скорректировать", callback_data="wo:edit"),
            ],
            [
                InlineKeyboardButton(text="❤️ Пульс", callback_data="wo:hr"),
                InlineKeyboardButton(text="🕒 Другое время", callback_data="wo:time"),
            ],
            [cancel_button()],
        ]
    )


def workout_duration() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="15 мин", callback_data="wo:dur:15"),
            InlineKeyboardButton(text="30 мин", callback_data="wo:dur:30"),
            InlineKeyboardButton(text="45 мин", callback_data="wo:dur:45"),
        ],
        [
            InlineKeyboardButton(text="1 час", callback_data="wo:dur:60"),
            InlineKeyboardButton(text="1.5 часа", callback_data="wo:dur:90"),
            InlineKeyboardButton(text="2 часа", callback_data="wo:dur:120"),
        ],
        [InlineKeyboardButton(text="✍️ Другое", callback_data="wo:dur:other"), cancel_button()],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def workout_intensity() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Лёгкая — говорить легко", callback_data="wo:int:low")],
            [
                InlineKeyboardButton(
                    text="🟡 Средняя — дышу тяжело, говорю фразами",
                    callback_data="wo:int:moderate",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔴 Высокая — говорить почти не мог(ла)", callback_data="wo:int:high"
                )
            ],
            [cancel_button()],
        ]
    )


def workout_sweat() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, заметно", callback_data="wo:sweat:yes"),
                InlineKeyboardButton(text="Слегка", callback_data="wo:sweat:light"),
                InlineKeyboardButton(text="Нет", callback_data="wo:sweat:no"),
            ],
            [cancel_button()],
        ]
    )


def confirm_delete() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Нет, оставить", callback_data="del:no")],
            [InlineKeyboardButton(text="🗑 Да, удалить всё", callback_data="del:yes")],
        ]
    )


def tag_button_label(tag: str) -> str:
    return tag_label(tag)


__all__ = [
    "CANCEL_DATA",
    "CANCEL_TEXT",
    "KIND_ICONS",
    "MENU_ROWS",
    "KIND_TABS",
    "PIN_BUTTONS_LIMIT",
    "PIN_LABEL_LIMIT",
    "activity_picker",
    "body_menu",
    "cancel_button",
    "cancel_only",
    "confirm_delete",
    "confirm_measurement",
    "confirm_glucose",
    "confirm_labs",
    "confirm_meal",
    "confirm_medication",
    "confirm_workout",
    "dictionary_page",
    "dictionary_pins",
    "dictionary_suggestions",
    "feature_hint",
    "health_setup",
    "hidden_features",
    "main_menu",
    "focus_picker",
    "onboarding_meals_picker",
    "onboarding_pregnancy_picker",
    "onboarding_sex_picker",
    "diabetes_picker",
    "glucose_log_button",
    "glucose_method_picker",
    "onboarding_skip",
    "photo_kind",
    "plate_meals_picker",
    "plate_settings",
    "pregnancy_picker",
    "product_actions",
    "rate_picker",
    "sex_picker",
    "sleep_setup",
    "stats_windows",
    "symptom_picker",
    "tag_button_label",
    "weight_prompt",
    "wellbeing_score",
    "workout_duration",
    "workout_intensity",
    "workout_sweat",
]
