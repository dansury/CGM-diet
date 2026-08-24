"""Keep recognition alive when the primary model answers 429.

Free models are rate-limited upstream constantly; a bare 429 in the middle of
«фото тарелки → карточка» reads to the user as a broken bot. The chain retries
the same request on the next model and only raises when every link failed.

A chain never leaves a free model for a paid one — alternates come from the
free catalogue only (`spec/models.md`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from src.llm.base import (
    ChatMessage,
    Completion,
    ImagePart,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from src.logging_setup import get_logger

log = get_logger("llm.fallback")

MAX_FALLBACKS = 2

_SWITCHABLE = (LLMRateLimitError, LLMTimeoutError)


class FallbackLLMClient:
    """`LLMClient` wrapper: primary first, then up to `MAX_FALLBACKS` alternates."""

    provider: ClassVar[str] = "fallback"

    def __init__(
        self,
        primary: LLMClient,
        *,
        primary_model: str | None = None,
        alternates: list[tuple[LLMClient, str]] | None = None,
    ) -> None:
        self._chain: list[tuple[LLMClient, str | None]] = [(primary, primary_model)]
        self._chain.extend((alternates or [])[:MAX_FALLBACKS])
        self.provider = getattr(primary, "provider", "fallback")

    @property
    def models(self) -> list[str | None]:
        return [model for _client, model in self._chain]

    async def _run(self, method: str, *args: Any, **kwargs: Any) -> Any:
        last: Exception | None = None
        for index, (client, model) in enumerate(self._chain):
            call_kwargs = dict(kwargs)
            if model is not None:
                call_kwargs["model"] = model
            try:
                return await getattr(client, method)(*args, **call_kwargs)
            except _SWITCHABLE as exc:
                last = exc
                nxt = self._chain[index + 1][1] if index + 1 < len(self._chain) else None
                if nxt is None:
                    break
                log.warning(
                    "llm.fallback.switching method=%s failed_model=%s next_model=%s (%s)",
                    method,
                    model,
                    nxt,
                    exc,
                )
            except LLMError:
                raise
        assert last is not None
        raise last

    async def chat(self, messages: list[ChatMessage], **kwargs: Any) -> Completion:
        return await self._run("chat", messages, **kwargs)

    async def vision(self, images: list[ImagePart], prompt: str, **kwargs: Any) -> Completion:
        return await self._run("vision", images, prompt, **kwargs)

    async def transcribe(self, audio: bytes, mime: str = "audio/ogg", **kwargs: Any) -> str:
        # speech-to-text runs on its own endpoint; no chain, no model juggling
        return await self._chain[0][0].transcribe(audio, mime, **kwargs)

    async def aclose(self) -> None:
        # clients are shared with the factory — closing them here would break
        # every other caller holding the same instance
        return None


def build_chain(
    primary: LLMClient, primary_model: str | None, free_ids: list[str]
) -> LLMClient:
    """Wrap `primary` when there is anything to fall back to, otherwise pass through."""
    alternates = [
        (primary, model_id) for model_id in free_ids if model_id and model_id != primary_model
    ][:MAX_FALLBACKS]
    if not alternates:
        return primary
    return FallbackLLMClient(primary, primary_model=primary_model, alternates=alternates)


__all__ = ["MAX_FALLBACKS", "FallbackLLMClient", "build_chain"]
