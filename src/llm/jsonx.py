"""Tolerant JSON extraction from model output. See `spec/llm.md` § JSON mode."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Return the first JSON value found in `text`.

    Models wrap JSON in ``` fences, prepend prose, or emit trailing commas.
    Raises ValueError when nothing parseable is present — callers decide
    whether that is a user-visible failure or a retry.
    """
    if not text:
        raise ValueError("empty model output")
    candidates: list[str] = []
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
    for candidate in candidates:
        for attempt in (candidate, _strip_trailing_commas(candidate)):
            try:
                return json.loads(attempt)
            except (json.JSONDecodeError, TypeError):
                continue
    raise ValueError("no JSON object found in model output")


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


__all__ = ["extract_json"]
