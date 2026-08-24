"""Charts as raw PNG bytes — no temp files, no disk.

The handler hands the bytes straight to `BufferedInputFile`, so nothing here
writes to the filesystem (a container with a read-only rootfs still works, and
two users rendering at once cannot collide on a path).

Timestamps arrive already converted to the user's local zone by
`handlers/reports.py`; this module never touches timezones. See `spec/charts.md`.
"""

from __future__ import annotations

import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # headless: must be set before pyplot is imported

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from src.analytics.stats import KeyStats  # noqa: E402

TARGET_LOW = 3.9
TARGET_HIGH = 10.0

CONFIDENCE_COLORS = {"high": "#c0392b", "medium": "#e67e22", "low": "#95a5a6"}

LABEL_LIMIT = 22
DPI = 130


def _flush(fig: plt.Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


def _short(label: str, limit: int = LABEL_LIMIT) -> str:
    label = (label or "").strip()
    return label if len(label) <= limit else label[: limit - 1] + "…"


def _empty(message: str) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3))
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=13, color="#7f8c8d")
    ax.axis("off")
    return _flush(fig)


def _time_axis(ax: plt.Axes) -> None:
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    for label in ax.get_xticklabels():
        label.set_rotation(30)
        label.set_horizontalalignment("right")


def render_timeline(
    points: list,
    meals: list[tuple[datetime, str]] | None = None,
    *,
    title: str | None = None,
    checkins: list[tuple[datetime, int]] | None = None,
) -> bytes:
    """Glucose curve, target band, meal markers and an optional wellbeing axis."""
    meals = meals or []
    checkins = checkins or []
    if not points and not meals:
        return _empty("Пока нечего рисовать")

    fig, ax = plt.subplots(figsize=(9, 4.2))
    if points:
        xs = [p.at for p in points]
        ys = [p.value for p in points]
        ax.axhspan(TARGET_LOW, TARGET_HIGH, color="#2ecc71", alpha=0.12, zorder=0)
        ax.plot(xs, ys, color="#2c3e50", linewidth=1.8, zorder=3)
        ax.scatter(xs, ys, s=10, color="#2c3e50", zorder=4)
        ax.set_ylabel("ммоль/л")

    top = max((p.value for p in points), default=TARGET_HIGH)
    for at, label in meals:
        ax.axvline(at, color="#8e44ad", linestyle="--", linewidth=1.0, alpha=0.7, zorder=2)
        ax.annotate(
            _short(label),
            xy=(at, top),
            xytext=(3, -6),
            textcoords="offset points",
            fontsize=8,
            color="#8e44ad",
            rotation=90,
            va="top",
        )

    if checkins:
        twin = ax.twinx()
        twin.plot(
            [at for at, _ in checkins],
            [score for _, score in checkins],
            color="#f39c12",
            marker="o",
            markersize=4,
            linewidth=1.2,
            alpha=0.85,
        )
        twin.set_ylim(0.5, 5.5)
        twin.set_ylabel("самочувствие 1–5", color="#f39c12")
        twin.tick_params(axis="y", colors="#f39c12")

    ax.set_title(title or "Глюкоза и приёмы пищи")
    ax.grid(True, alpha=0.2)
    _time_axis(ax)
    return _flush(fig)


def render_ranking(
    stats: list[KeyStats], *, limit: int = 10, unit_label: str = "ммоль/л"
) -> bytes:
    """Horizontal bars of the mean rise per component, coloured by confidence."""
    rows = sorted(stats, key=lambda s: s.mean_delta, reverse=True)[:limit]
    if not rows:
        return _empty("Недостаточно данных для рейтинга")

    labels = [_short(row.label or row.key) for row in rows][::-1]
    values = [row.mean_delta for row in rows][::-1]
    colors = [CONFIDENCE_COLORS.get(row.confidence, "#95a5a6") for row in rows][::-1]

    fig, ax = plt.subplots(figsize=(8, max(2.4, 0.45 * len(rows) + 1.2)))
    bars = ax.barh(labels, values, color=colors)
    span = max(values) if values else 1.0
    for bar, row in zip(bars, rows[::-1], strict=True):
        ax.annotate(
            f"+{row.mean_delta:.1f} (n={row.n})",
            xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color="#2c3e50",
        )
    ax.set_xlim(0, span * 1.35 if span else 1.0)
    ax.set_xlabel(f"средний подъём, {unit_label}")
    ax.set_title("Подъём сахара после компонентов")
    ax.grid(True, axis="x", alpha=0.2)
    return _flush(fig)


def render_wellbeing(
    series: list[tuple[datetime, int]],
    *,
    symptom_series: dict[str, list[tuple[datetime, int]]] | None = None,
    title: str | None = None,
) -> bytes:
    """Wellbeing score over time plus triangle markers where symptoms were noted."""
    if not series:
        return _empty("Пока нет отметок самочувствия")

    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot(
        [at for at, _ in series],
        [score for _, score in series],
        color="#f39c12",
        marker="o",
        markersize=5,
        linewidth=1.6,
    )
    ax.set_ylim(0.5, 5.5)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_ylabel("оценка 1–5")

    for index, (name, marks) in enumerate((symptom_series or {}).items()):
        stamps = [at for at, flag in marks if flag]
        if not stamps:
            continue
        level = 0.9 - index * 0.12
        ax.scatter(
            stamps,
            [level] * len(stamps),
            marker="^",
            s=40,
            label=_short(name),
            zorder=5,
        )
    if symptom_series:
        ax.legend(loc="upper right", fontsize=8, framealpha=0.8)

    ax.set_title(title or "Самочувствие")
    ax.grid(True, alpha=0.2)
    _time_axis(ax)
    return _flush(fig)


def render_weight(
    series: list[tuple[datetime, float]],
    *,
    target_kg: float | None = None,
    corridor: list[tuple[datetime, float]] | None = None,
    title: str | None = None,
) -> bytes:
    """Замеры веса, линия цели и коридор ожидаемого темпа (`spec/body.md`)."""
    if not series:
        return _empty("Пока нет замеров веса")

    fig, ax = plt.subplots(figsize=(9, 3.8))
    xs = [at for at, _ in series]
    ys = [kg for _, kg in series]
    ax.plot(xs, ys, color="#2c3e50", marker="o", markersize=5, linewidth=1.8, zorder=4)
    if corridor:
        ax.plot(
            [at for at, _ in corridor],
            [kg for _, kg in corridor],
            color="#2980b9",
            linestyle="--",
            linewidth=1.2,
            alpha=0.8,
            label="ожидаемый темп",
            zorder=2,
        )
    if target_kg:
        ax.axhline(
            target_kg, color="#27ae60", linestyle=":", linewidth=1.6, label="цель", zorder=3
        )
    if corridor or target_kg:
        ax.legend(loc="best", fontsize=8, framealpha=0.8)
    ax.set_ylabel("кг")
    ax.set_title(title or "Вес")
    ax.grid(True, alpha=0.2)
    _time_axis(ax)
    return _flush(fig)


def render_body_composition(
    fat: list[tuple[datetime, float]],
    muscle: list[tuple[datetime, float]],
    *,
    title: str | None = None,
) -> bytes:
    """Процент жира и мышечная масса; рисуется только по введённому биоимпедансу."""
    if len(fat) < 2 and len(muscle) < 2:
        return _empty("Биоимпеданс пока не вводился")

    fig, ax = plt.subplots(figsize=(9, 3.8))
    drawn = False
    if len(fat) >= 2:
        ax.plot(
            [at for at, _ in fat],
            [value for _, value in fat],
            color="#e67e22",
            marker="o",
            markersize=4,
            linewidth=1.6,
            label="жир, %",
        )
        ax.set_ylabel("жир, %", color="#e67e22")
        ax.tick_params(axis="y", colors="#e67e22")
        drawn = True
    if len(muscle) >= 2:
        axis = ax.twinx() if drawn else ax
        axis.plot(
            [at for at, _ in muscle],
            [value for _, value in muscle],
            color="#16a085",
            marker="s",
            markersize=4,
            linewidth=1.6,
            label="мышцы, кг",
        )
        axis.set_ylabel("мышцы, кг", color="#16a085")
        axis.tick_params(axis="y", colors="#16a085")
    ax.set_title(title or "Состав тела")
    ax.grid(True, alpha=0.2)
    _time_axis(ax)
    return _flush(fig)


__all__ = [
    "CONFIDENCE_COLORS",
    "TARGET_HIGH",
    "TARGET_LOW",
    "render_body_composition",
    "render_ranking",
    "render_timeline",
    "render_weight",
    "render_wellbeing",
]
