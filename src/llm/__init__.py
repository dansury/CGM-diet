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


def build_client(settings: Settings | None = None) -> LLMClient:
    s = settings or load_settings()
    if s.llm_mock or not s.openrouter_api_key:
        return MockClient(s)
    from src.llm.openrouter import OpenRouterClient

    return OpenRouterClient(s)


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
]
