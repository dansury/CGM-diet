"""OpenRouter client — OpenAI-compatible /chat/completions with vision parts.

Retries: 3 attempts on transient upstream statuses with exponential backoff and
jitter; a 429 backs off harder and honours Retry-After (capped).
See `spec/llm.md` § OpenRouter.
"""

from __future__ import annotations

import asyncio
import base64
import random
from typing import Any, ClassVar

import httpx

from src.config import Settings
from src.llm.base import (
    ChatMessage,
    Completion,
    ImagePart,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from src.logging_setup import get_logger

_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0
_RATE_LIMIT_BACKOFF_BASE = 2.0
_RATE_LIMIT_WAIT_CAP = 20.0

APP_TITLE = "cgm-diet"
APP_URL = "https://github.com/dansury/CGM-diet"


class OpenRouterClient:
    provider: ClassVar[str] = "openrouter"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        if not settings.openrouter_api_key:
            raise LLMConfigError("OPENROUTER_API_KEY is not set")
        self._settings = settings
        self._base_url = settings.openrouter_base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": APP_URL,
            "X-Title": APP_TITLE,
        }
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url, timeout=httpx.Timeout(90.0, connect=10.0)
        )
        self._log = get_logger("llm.openrouter")

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Completion:
        model = model or self._settings.text_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        data = await self._post("/chat/completions", payload, model=model)
        return self._parse(data, model)

    async def vision(
        self,
        images: list[ImagePart],
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 1200,
    ) -> Completion:
        """One multimodal turn: N images plus an instruction.

        Two-sided label scans arrive as two photos, so `images` is a list —
        the model sees front and back in the same turn and can merge them.
        """
        if not images:
            raise LLMError("vision() requires at least one image")
        model = model or self._settings.vision_model
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(img.data).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{img.mime};base64,{b64}"}}
            )
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": 0.1}
        data = await self._post("/chat/completions", payload, model=model)
        return self._parse(data, model)

    async def transcribe(
        self, audio: bytes, mime: str = "audio/ogg", *, model: str | None = None
    ) -> str:
        """Speech-to-text via an OpenAI-compatible /audio/transcriptions endpoint."""
        s = self._settings
        if not (s.stt_base_url and s.stt_api_key):
            raise LLMConfigError("STT_BASE_URL / STT_API_KEY are not set")
        url = s.stt_base_url.rstrip("/") + "/audio/transcriptions"
        suffix = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/wav": "wav"}.get(mime, "ogg")
        files = {"file": (f"voice.{suffix}", audio, mime)}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {s.stt_api_key}"},
                data={"model": model or s.stt_model},
                files=files,
            )
        if resp.status_code >= 400:
            raise LLMError(f"stt failed: {resp.status_code} {resp.text[:200]}")
        body = resp.json()
        return (body.get("text") or "").strip()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---- internals -------------------------------------------------------

    async def _post(self, path: str, payload: dict[str, Any], *, model: str) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.post(path, json=payload, headers=self._headers)
            except httpx.TimeoutException:
                last_exc = LLMTimeoutError(f"{model}: timeout")
                await self._sleep(attempt)
                continue
            except httpx.HTTPError as exc:
                last_exc = LLMError(f"{model}: transport error: {exc}")
                await self._sleep(attempt)
                continue
            if resp.status_code in _RETRY_STATUSES:
                last_exc = (
                    LLMRateLimitError(f"{model}: rate limited")
                    if resp.status_code == 429
                    else LLMError(f"{model}: upstream {resp.status_code}")
                )
                if attempt == _MAX_ATTEMPTS:
                    break
                await self._sleep(attempt, resp=resp)
                continue
            if resp.status_code >= 400:
                raise LLMError(f"{model}: {resp.status_code} {resp.text[:300]}")
            return resp.json()
        raise last_exc or LLMError(f"{model}: request failed")

    async def _sleep(self, attempt: int, *, resp: httpx.Response | None = None) -> None:
        if resp is not None and resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    await asyncio.sleep(min(float(retry_after), _RATE_LIMIT_WAIT_CAP))
                    return
                except ValueError:
                    pass
            delay = min(_RATE_LIMIT_BACKOFF_BASE * (2 ** (attempt - 1)), _RATE_LIMIT_WAIT_CAP)
        else:
            delay = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_CAP)
        await asyncio.sleep(delay * (0.5 + random.random()))

    @staticmethod
    def _parse(data: dict[str, Any], model: str) -> Completion:
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{model}: malformed response") from exc
        if isinstance(text, list):  # some providers return content parts
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        usage = data.get("usage") or {}
        return Completion(
            text=text.strip(),
            model=data.get("model") or model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            raw=data,
        )


__all__ = ["OpenRouterClient"]
