"""Structured logging + the ERROR-tap the owner's reports hang off.

Beyond formatting, this module keeps the last WARNING+ records in memory (what
`/errors` shows) and forwards ERROR+ ones to a pluggable sink — that sink is
`errors_report.forward_log_event`, which is the only way a fail-soft path (log
and degrade, never raise) can reach the owner. See `spec/infra.md` § Logging
and `spec/errors.md`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import deque
from collections.abc import Callable
from typing import Any

_CONFIGURED = False

RECENT_CAPACITY = 50
_RECENT: deque[logging.LogRecord] = deque(maxlen=RECENT_CAPACITY)
_FORWARDER: Callable[[logging.LogRecord], None] | None = None


class _CaptureHandler(logging.Handler):
    """Ring buffer of recent WARNING+ records, plus the ERROR+ forwarder tap."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        _RECENT.append(record)
        if record.levelno >= logging.ERROR and _FORWARDER is not None:
            try:
                _FORWARDER(record)
            except Exception:  # a broken sink must not break logging
                pass


def set_error_forwarder(sink: Callable[[logging.LogRecord], None] | None) -> None:
    global _FORWARDER
    _FORWARDER = sink


def recent_errors(limit: int = 10) -> list[logging.LogRecord]:
    """Newest first."""
    return list(_RECENT)[-limit:][::-1]


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str | None = None, json_output: bool | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    if json_output is None:
        json_output = (os.getenv("LOG_JSON") or "").lower() in {"1", "true", "yes", "on"}
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        _JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s", "%H:%M:%S")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler, _CaptureHandler()]
    root.setLevel(level)
    for noisy in ("httpx", "aiogram.event", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


__all__ = [
    "RECENT_CAPACITY",
    "get_logger",
    "recent_errors",
    "set_error_forwarder",
    "setup_logging",
]
