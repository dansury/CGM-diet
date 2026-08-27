"""What the user came for: a small, closed list of goals picked at first run.

The questionnaire opens with this list (`spec/onboarding.md`) because every
later question reads differently depending on the answer: a target weight is
worth asking only from someone who came to change their weight, and the one
feature the bot tells about later should be the one that serves the goal the
person named. Multiple choice — people rarely have exactly one reason — plus a
free-form variant that is stored verbatim and never parsed.

The list is not a diagnosis and not a segmentation of health: it only orders
what the bot says first. See `spec/onboarding.md` § Цели.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

#: ключ свободного варианта — текст лежит в `body_profile.focus_note`
CUSTOM = "custom"
#: сколько символов своего варианта храним
NOTE_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Goal:
    key: str
    title: str
    #: возможности (`src/features.py`), которые этой цели служат напрямую
    features: tuple[str, ...] = ()


GOALS: tuple[Goal, ...] = (
    Goal(key="weight", title="Снизить вес", features=("body", "plate")),
    Goal(key="sugar", title="Держать сахар в норме", features=("stats", "graph", "health")),
    Goal(
        key="energy",
        title="Больше энергии, меньше сонливости после еды",
        features=("wellbeing", "stats"),
    ),
    Goal(key="habits", title="Наладить питание", features=("plate", "dictionary")),
    Goal(
        key="symptoms",
        title="Понять, от каких продуктов плохо",
        features=("wellbeing", "meds"),
    ),
    Goal(key="labs", title="Улучшить показатели анализов", features=("labs",)),
    Goal(key="muscle", title="Набрать вес и мышцы", features=("body", "workout")),
    Goal(key="sport", title="Форма и выносливость", features=("workout", "health")),
)

BY_KEY: dict[str, Goal] = {goal.key: goal for goal in GOALS}

#: цели, ради которых имеет смысл спрашивать целевой вес
WEIGHT_GOALS = frozenset({"weight", "muscle"})


def decode(raw: str | None) -> tuple[str, ...]:
    """Строка из БД → ключи в порядке каталога; чужое молча отбрасываем."""
    if not raw:
        return ()
    picked = {part.strip() for part in raw.split(",") if part.strip()}
    known = tuple(goal.key for goal in GOALS if goal.key in picked)
    return known + ((CUSTOM,) if CUSTOM in picked else ())


def encode(keys: Iterable[str]) -> str:
    """Ключи → строка для БД. Пустой выбор — пустая строка, не `None`:
    «целей не назвал» и «ещё не спрашивали» — разные состояния."""
    return ",".join(decode(",".join(keys)))


def titles(keys: Iterable[str], *, note: str | None = None) -> list[str]:
    """Человеческие названия выбранных целей; свой вариант — как написан."""
    labels = [BY_KEY[key].title for key in decode(",".join(keys)) if key in BY_KEY]
    if CUSTOM in decode(",".join(keys)) and note:
        labels.append(note)
    return labels


def wants_weight_goal(keys: Iterable[str]) -> bool:
    """Спрашивать ли целевой вес.

    Никаких целей не названо — спрашиваем как раньше: молчание не значит
    «вес не интересует». Названы цели, и веса среди них нет — не спрашиваем:
    человек пришёл за сахаром, а не за весами (задать цель можно в /body).
    """
    picked = set(decode(",".join(keys)))
    if not picked:
        return True
    return bool(picked & WEIGHT_GOALS)


def feature_order(keys: Iterable[str]) -> tuple[str, ...]:
    """Возможности, которые стоит показать первыми — по названным целям."""
    order: list[str] = []
    for key in decode(",".join(keys)):
        for feature_key in BY_KEY[key].features if key in BY_KEY else ():
            if feature_key not in order:
                order.append(feature_key)
    return tuple(order)


def normalize_note(text: str | None) -> str | None:
    cleaned = " ".join((text or "").split())
    return cleaned[:NOTE_LIMIT] or None


__all__ = [
    "BY_KEY",
    "CUSTOM",
    "GOALS",
    "NOTE_LIMIT",
    "WEIGHT_GOALS",
    "Goal",
    "decode",
    "encode",
    "feature_order",
    "normalize_note",
    "titles",
    "wants_weight_goal",
]
