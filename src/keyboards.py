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
        ],
        resize_keyboard=True,
        input_field_placeholder="Пришлите фото, текст или голосовое",
    )


def confirm_meal() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Верно", callback_data="meal:ok"),
                InlineKeyboardButton(text="✏️ Исправить", callback_data="meal:edit"),
            ],
            [
                InlineKeyboardButton(text="🕒 Другое время", callback_data="meal:time"),
                InlineKeyboardButton(text="🗑 Отменить", callback_data="meal:drop"),
            ],
            [InlineKeyboardButton(text="🤖 Это не еда", callback_data="photo:reroute")],
        ]
    )


def confirm_glucose() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="glu:ok"),
                InlineKeyboardButton(text="✏️ Исправить", callback_data="glu:edit"),
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
                InlineKeyboardButton(text="✅ Сохранить", callback_data="lab:ok"),
                InlineKeyboardButton(text="🗑 Отменить", callback_data="lab:drop"),
            ]
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
    rows.append([InlineKeyboardButton(text="🗑 Отменить", callback_data="prod:drop")])
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
    "confirm_delete",
    "confirm_glucose",
    "confirm_labs",
    "confirm_meal",
    "main_menu",
    "photo_kind",
    "product_actions",
    "stats_windows",
    "symptom_picker",
    "tag_button_label",
    "wellbeing_score",
]
