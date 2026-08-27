"""The sugar track: what someone who came to keep their glucose in range is asked.

Picking «Держать сахар в норме» among the goals (`src/goals.py`) turns three
extra questions on — diabetes, medication, how the person measures — and, if
they measure at all, the per-meal offer to send a reading. Someone who did not
come for sugar is never asked for a reading: the offer would be noise.

The catalogues here are plain data with no ORM and no aiogram, so the
questionnaire, the keyboards and the tests all read the same lists. Nothing in
this module interprets an answer: the type of diabetes and the medication are
context the user typed, never a diagnosis and never a dose
(`.specify/memory/constitution.md`, принцип I). See `spec/onboarding.md`
§ Сахарный трек.
"""

from __future__ import annotations

from collections.abc import Iterable

#: цель из `src/goals.py`, которая включает весь трек
FOCUS_KEY = "sugar"
#: «никакими способами не меряю» — исключающий вариант, не хранится как способ
NONE = "none"
#: сколько символов свободного ответа про лекарства храним
MEDS_LIMIT = 500

#: диабет — один выбор; порядок совпадает с порядком кнопок
DIABETES: tuple[tuple[str, str], ...] = (
    ("t1", "Диабет 1 типа"),
    ("t2", "Диабет 2 типа"),
    ("pre", "Преддиабет"),
    ("gest", "Гестационный диабет"),
    ("no", "Нет диабета"),
    ("unknown", "Не знаю"),
)

#: чем человек меряет сахар — множественный выбор
METHODS: tuple[tuple[str, str], ...] = (
    ("meter", "Глюкометр"),
    ("cgm", "Носимый датчик (CGM)"),
)

DIABETES_BY_KEY: dict[str, str] = dict(DIABETES)
METHODS_BY_KEY: dict[str, str] = dict(METHODS)

#: ответы, при которых «есть что отслеживать» — влияет только на формулировки
DIAGNOSED = frozenset({"t1", "t2", "pre", "gest"})


def wants_sugar_track(focus_keys: Iterable[str]) -> bool:
    """Отмечена ли цель «держать сахар в норме»."""
    return FOCUS_KEY in set(focus_keys or ())


def decode_methods(raw: str | None) -> tuple[str, ...]:
    """Строка из БД → ключи способов в порядке каталога; чужое отброшено."""
    if not raw:
        return ()
    picked = {part.strip() for part in raw.split(",") if part.strip()}
    return tuple(key for key, _ in METHODS if key in picked)


def encode_methods(keys: Iterable[str]) -> str:
    """Ключи → строка для БД. Пустая строка — «спросили, ничем не меряет»;
    NULL в колонке значит «не спрашивали» — это разные состояния."""
    return ",".join(decode_methods(",".join(keys)))


def tracks_glucose(value: str | Iterable[str] | None) -> bool:
    """Меряет ли человек сахар хоть чем-нибудь — строкой из БД или списком."""
    raw = value if isinstance(value, str) or value is None else ",".join(value)
    return bool(decode_methods(raw))


def diabetes_title(key: str | None) -> str | None:
    return DIABETES_BY_KEY.get(key or "")


def method_titles(value: str | Iterable[str] | None) -> list[str]:
    raw = value if isinstance(value, str) or value is None else ",".join(value)
    return [METHODS_BY_KEY[key] for key in decode_methods(raw)]


def normalize_meds(text: str | None) -> str | None:
    """Свободный ответ про лекарства: «нет»/пусто → None, остальное как есть."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return None
    if cleaned.strip(" .!").casefold() in {"нет", "никакие", "не принимаю", "-", "—", "no"}:
        return None
    return cleaned[:MEDS_LIMIT]


__all__ = [
    "DIABETES",
    "DIABETES_BY_KEY",
    "DIAGNOSED",
    "FOCUS_KEY",
    "MEDS_LIMIT",
    "METHODS",
    "METHODS_BY_KEY",
    "NONE",
    "decode_methods",
    "diabetes_title",
    "encode_methods",
    "method_titles",
    "normalize_meds",
    "tracks_glucose",
    "wants_sugar_track",
]
