"""Structured logging. See `spec/infra.md` § Logging."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

_CONFIGURED = False


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
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "aiogram.event", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


__all__ = ["get_logger", "setup_logging"]
