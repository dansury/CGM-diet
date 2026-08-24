"""LLM provider factory. `LLM_MOCK=true` or a missing key -> MockClient."""

from __future__ import annotations

from src.config import Settings, load_settings
from src.llm.base import (
    ChatMessage,
    Completion,
    ImagePart,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from src.llm.jsonx import extract_json
from src.llm.mock import MockClient

_client: LLMClient | None = None
_free_alternates: list[str] = []


def set_free_alternates(model_ids: list[str]) -> None:
    """Free model ids to retry on after a 429 (`spec/models.md` § Фолбэк).

    Filled at startup from the shir-man catalogue; changing it rebuilds the
    process client on the next `get_client()`.
    """
    global _client
    _free_alternates[:] = [m for m in model_ids if m]
    _client = None


def build_client(settings: Settings | None = None) -> LLMClient:
    s = settings or load_settings()
    if s.llm_mock or not s.openrouter_api_key:
        return MockClient(s)
    from src.llm.openrouter import OpenRouterClient

    client: LLMClient = OpenRouterClient(s)
    if s.free_fallback_enabled and _free_alternates:
        from src.llm.fallback import build_chain

        client = build_chain(client, None, list(_free_alternates))
    return client


def get_client(settings: Settings | None = None) -> LLMClient:
    global _client
    if _client is None:
        _client = build_client(settings)
    return _client


def reset_client(client: LLMClient | None = None) -> None:
    """Test hook: replace the process-global client."""
    global _client
    _client = client


__all__ = [
    "ChatMessage",
    "Completion",
    "ImagePart",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "MockClient",
    "build_client",
    "extract_json",
    "get_client",
    "reset_client",
    "set_free_alternates",
]
