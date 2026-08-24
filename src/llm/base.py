"""LLM transport-agnostic types. See `spec/llm.md`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LLMError(RuntimeError):
    """Base class for all LLM failures."""


class LLMConfigError(LLMError):
    """Missing/invalid credentials or model id."""


class LLMTimeoutError(LLMError):
    """Upstream did not answer in time."""


class LLMRateLimitError(LLMError):
    """429 / quota exhausted."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str  # system|user|assistant
    content: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    data: bytes
    mime: str = "image/jpeg"


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict = field(default_factory=dict)


class LLMClient(Protocol):
    """Everything the bot needs from a model provider."""

    provider: str

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Completion: ...

    async def vision(
        self,
        images: list[ImagePart],
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 1200,
    ) -> Completion: ...

    async def transcribe(self, audio: bytes, mime: str = "audio/ogg") -> str: ...

    async def aclose(self) -> None: ...


__all__ = [
    "ChatMessage",
    "Completion",
    "ImagePart",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMRateLimitError",
    "LLMTimeoutError",
]
