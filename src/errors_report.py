"""One error, two audiences.

The owner gets everything — surface, call site, user, context, full traceback —
inside a single Telegram `<pre><code>` block, which Telegram renders with a
one-tap copy button. The user gets one sentence and no traceback, ever.

Ported from GrowthProducer (`spec/errors.md`); the dedupe/throttle/noise rules
are what keep an error *loop* from turning into a flood of DMs.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import time
import traceback as tb
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.config import Settings
from src.logging_setup import get_logger

log = get_logger("errors_report")

DEDUPE_WINDOW_S = 300
MAX_PER_MINUTE = 8
DEFAULT_LIMIT = 3500
RECENT_LIMIT = 20

#: self-healing infra churn: the bot already survives these on its own, and
#: they arrive in bursts. Logged and kept in `/errors`, never DM'd.
_NOISE_MARKERS = (
    "telegramservererror",
    "telegramnetworkerror",
    "telegramretryafter",
    "bad gateway",
    "gateway time-out",
    "gateway timeout",
    "service unavailable",
    "server disconnected",
    "failed to fetch updates",
    "http 429",
    "llmratelimiterror",
    "readtimeout",
    "connecttimeout",
)

#: loggers whose own failures must not report themselves forever
_SELF_LOGGERS = ("errors_report", "handlers.errors", "notifications")


@dataclass(frozen=True, slots=True)
class ErrorReport:
    source: str  # bot|web|task|log
    where: str
    error: str
    traceback: str | None = None
    user: str | None = None
    context: dict[str, str] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


def describe_exc(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def fingerprint(report: ErrorReport) -> str:
    """What makes two reports «the same bug»: surface, call site, type, raise site."""
    last_frame = ""
    if report.traceback:
        frames = [line for line in report.traceback.splitlines() if line.strip().startswith("File")]
        last_frame = frames[-1].strip() if frames else ""
    exc_type = report.error.split(":", 1)[0].strip()
    raw = f"{report.source}|{report.where}|{exc_type}|{last_frame}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _elide_middle(text: str, budget: int) -> str:
    """Keep the entry point and the raise site; drop the middle.

    Both ends are needed to fix a bug — a tail-only traceback hides which
    handler got there.
    """
    if budget <= 0:
        return ""
    lines = text.splitlines()
    if len(text) <= budget:
        return text
    head: list[str] = []
    tail: list[str] = []
    size = 0
    i, j = 0, len(lines) - 1
    while i <= j:
        if len(head) <= len(tail):
            candidate = lines[i]
            if size + len(candidate) + 1 > budget - 40:
                break
            head.append(candidate)
            i += 1
        else:
            candidate = lines[j]
            if size + len(candidate) + 1 > budget - 40:
                break
            tail.insert(0, candidate)
            j -= 1
        size += len(candidate) + 1
    skipped = len(lines) - len(head) - len(tail)
    if skipped <= 0:
        return text[:budget]
    return "\n".join([*head, f"… {skipped} строк пропущено …", *tail])


def render_report(
    report: ErrorReport, *, repeats: int = 0, suppressed: int = 0, limit: int = DEFAULT_LIMIT
) -> str:
    header = f"🔴 <b>{html.escape(report.source)}</b> · <code>{html.escape(report.where)}</code>"
    if repeats:
        header += f" ×{repeats}"

    body_lines = [
        f"CGM-diet error [{fingerprint(report)}]",
        f"time: {report.ts:%Y-%m-%d %H:%M:%S} UTC",
        f"source: {report.source}",
        f"where: {report.where}",
    ]
    if report.user:
        body_lines.append(f"user: {report.user}")
    if repeats:
        body_lines.append(f"repeats: {repeats} за последние {DEDUPE_WINDOW_S // 60} мин")
    if suppressed:
        body_lines.append(f"suppressed: {suppressed} (rate limit)")
    for key, value in report.context.items():
        body_lines.append(f"{key}: {str(value)[:200]}")
    body_lines.append(f"error: {report.error}")

    body = "\n".join(body_lines)
    if report.traceback:
        room = limit - len(body) - 2
        body = body + "\n\n" + _elide_middle(report.traceback.strip(), room)
    return f"{header}\n<pre><code class=\"language-log\">{html.escape(body[:limit])}</code></pre>"


def is_transient_noise(where: str, error: str) -> bool:
    haystack = f"{where}\n{error}".lower()
    return any(marker in haystack for marker in _NOISE_MARKERS)


# ------------------------------------------------------------------ delivery


class _State:
    bot: Any = None
    settings: Settings | None = None
    seen: dict[str, tuple[float, int]]
    sent_at: deque[float]
    recent: deque[ErrorReport]
    suppressed: int = 0

    def __init__(self) -> None:
        self.seen = {}
        self.sent_at = deque(maxlen=MAX_PER_MINUTE * 4)
        self.recent = deque(maxlen=RECENT_LIMIT)


_state = _State()


def wire_error_reporter(bot: Any, settings: Settings) -> None:
    """Bind the delivery channel and start forwarding ERROR+ log records."""
    from src.logging_setup import set_error_forwarder

    _state.bot = bot
    _state.settings = settings
    set_error_forwarder(forward_log_event)


def reset_error_reporter() -> None:
    """Test hook: forget bot, dedupe state and throttle counters."""
    from src.logging_setup import set_error_forwarder

    _state.bot = None
    _state.settings = None
    _state.seen.clear()
    _state.sent_at.clear()
    _state.recent.clear()
    _state.suppressed = 0
    set_error_forwarder(None)


def recent_reports(limit: int = 10) -> list[ErrorReport]:
    return list(_state.recent)[-limit:][::-1]


def _throttled(now: float) -> bool:
    while _state.sent_at and now - _state.sent_at[0] > 60:
        _state.sent_at.popleft()
    return len(_state.sent_at) >= MAX_PER_MINUTE


async def report_error(
    *,
    source: str,
    where: str,
    exc: BaseException | None = None,
    error: str | None = None,
    traceback_text: str | None = None,
    user: str | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """Deliver one report. Never raises: reporting must not break a request."""
    try:
        if exc is not None:
            error = error or describe_exc(exc)
            traceback_text = traceback_text or "".join(
                tb.format_exception(type(exc), exc, exc.__traceback__)
            )
        report = ErrorReport(
            source=source,
            where=where,
            error=error or "unknown error",
            traceback=traceback_text,
            user=user,
            context={str(k): str(v)[:200] for k, v in (context or {}).items()},
        )
        _state.recent.append(report)

        if is_transient_noise(where, report.error):
            return "noise"
        settings = _state.settings
        if _state.bot is None or settings is None or not settings.error_reports_enabled:
            return "disabled"
        recipients = settings.error_recipients
        if not recipients:
            return "no_recipients"

        now = time.monotonic()
        key = fingerprint(report)
        first_at, repeats = _state.seen.get(key, (0.0, 0))
        if first_at and now - first_at < DEDUPE_WINDOW_S:
            _state.seen[key] = (first_at, repeats + 1)
            return "deduped"
        if _throttled(now):
            _state.suppressed += 1
            return "throttled"

        text = render_report(report, repeats=repeats, suppressed=_state.suppressed)
        _state.seen[key] = (now, 0)
        _state.sent_at.append(now)
        _state.suppressed = 0

        delivered = False
        for chat_id in recipients:
            try:
                await _state.bot.send_message(chat_id, text)
                delivered = True
            except Exception as send_exc:  # blocked bot, bad chat id, network
                log.warning("errors_report.send_failed chat=%s (%s)", chat_id, send_exc)
        return "sent" if delivered else "failed"
    except Exception:  # the reporter itself must never propagate
        log.warning("errors_report.failed", exc_info=True)
        return "failed"


def report_error_nowait(**kwargs: Any) -> None:
    """Fire-and-forget for sync call sites; no running loop → no-op."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(report_error(**kwargs))


def forward_log_event(record: Any) -> None:
    """`logging` ERROR+ → a report. The catch-all for fail-soft paths.

    A handler that catches, logs and degrades never raises, so no aiogram or
    HTTP hook would ever see it — this is the only place such a failure
    surfaces.
    """
    name = getattr(record, "name", "")
    if any(name.startswith(prefix) for prefix in _SELF_LOGGERS):
        return
    exc_info = getattr(record, "exc_info", None)
    traceback_text = "".join(tb.format_exception(*exc_info)) if exc_info else None
    where = f"{name} ({getattr(record, 'module', '?')}:{getattr(record, 'lineno', 0)})"
    report_error_nowait(
        source="log",
        where=where,
        error=record.getMessage(),
        traceback_text=traceback_text,
    )


__all__ = [
    "DEDUPE_WINDOW_S",
    "MAX_PER_MINUTE",
    "ErrorReport",
    "describe_exc",
    "fingerprint",
    "forward_log_event",
    "is_transient_noise",
    "recent_reports",
    "render_report",
    "report_error",
    "report_error_nowait",
    "reset_error_reporter",
    "wire_error_reporter",
]
