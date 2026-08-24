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


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🍽 Записать еду"), KeyboardButton(text="🩸 Записать сахар")],
            [KeyboardButton(text="🛒 Проверить продукт"), KeyboardButton(text="🙂 Самочувствие")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📈 График")],
            [KeyboardButton(text="⭐️ Мой словарь"), KeyboardButton(text="💊 Лекарства")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Пришлите фото, текст или голосовое",
    )


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
            [InlineKeyboardButton(text="🗑 Отменить", callback_data="meal:drop")],
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
                InlineKeyboardButton(text="🗑 Отменить", callback_data="glu:drop"),
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
            [InlineKeyboardButton(text="🗑 Отменить", callback_data="lab:drop")],
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
                InlineKeyboardButton(text="🗑 Отменить", callback_data="med:drop"),
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
    rows.append(
        [
            InlineKeyboardButton(text="➕ Вторая сторона", callback_data="prod:more"),
            InlineKeyboardButton(text="💾 Только запомнить", callback_data="prod:save"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="✏️ БЖУ", callback_data="prod:macros"),
            InlineKeyboardButton(text="✏️ Скорректировать", callback_data="prod:edit"),
        ]
    )
    rows.append([InlineKeyboardButton(text="🗑 Отменить", callback_data="prod:drop")])
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


KIND_ICONS = {"meal": "🍽", "item": "🥄", "medication": "💊"}


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
    rows.append([InlineKeyboardButton(text="➕ Разобрать как новое", callback_data="dict:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    switch = [
        InlineKeyboardButton(
            text="🍽 Блюда" if kind != "meal" else "• 🍽 Блюда",
            callback_data="dict:page:meal:0",
        ),
        InlineKeyboardButton(
            text="💊 Лекарства" if kind != "medication" else "• 💊 Лекарства",
            callback_data="dict:page:medication:0",
        ),
    ]
    rows.append(switch)
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
            [InlineKeyboardButton(text="💊 Лекарство", callback_data="kind:medication")],
            [InlineKeyboardButton(text="🗑 Отменить", callback_data="kind:drop")],
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
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="wb:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    "KIND_ICONS",
    "confirm_delete",
    "confirm_glucose",
    "confirm_labs",
    "confirm_meal",
    "confirm_medication",
    "dictionary_page",
    "dictionary_suggestions",
    "health_setup",
    "main_menu",
    "photo_kind",
    "product_actions",
    "stats_windows",
    "symptom_picker",
    "tag_button_label",
    "wellbeing_score",
]
