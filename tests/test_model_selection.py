"""Model selection: two levels, and the 429 fallback chain."""

from __future__ import annotations

import pytest

from src.llm import model_selection as ms
from src.llm.base import Completion, LLMError, LLMRateLimitError
from src.llm.fallback import FallbackLLMClient, build_chain


def test_env_is_the_floor():
    resolved = ms.resolve_all({})
    assert set(resolved) == set(ms.SLOTS)
    assert all(item.level == "env" for item in resolved.values())


def test_a_slot_pick_beats_the_global_one():
    stored = {ms.KEY_GLOBAL: "openai/gpt-4o-mini", ms.KEY_SLOTS: {"vision": "openai/gpt-4o"}}
    resolved = ms.resolve_all(stored)
    assert resolved["vision"] == ms.Resolved("openai/gpt-4o", "slot")
    assert resolved["text"] == ms.Resolved("openai/gpt-4o-mini", "global")


def test_a_global_pick_never_lands_in_a_slot_that_cannot_run_it():
    # a chat model cannot transcribe an .ogg, whoever picked it "for everything"
    resolved = ms.resolve_all({ms.KEY_GLOBAL: "openai/gpt-4o-mini"})
    assert resolved["stt"].level == "env"


def test_catalogue_is_readable_for_every_slot():
    for slot in ms.SLOTS:
        assert ms.candidates(slot), slot


def test_unknown_slot_is_rejected():
    with pytest.raises(ms.UnknownSlot):
        ms.candidates("telepathy")


def test_process_cache_round_trip():
    ms.reset()
    assert ms.current("vision") is None
    ms.refresh({"vision": "google/gemini-2.5-pro", "text": ""})
    assert ms.current("vision") == "google/gemini-2.5-pro"
    assert ms.current("text") is None
    ms.reset()


# ------------------------------------------------------------------ fallback

class _Flaky:
    provider = "test"

    def __init__(self, fail_models: set[str]) -> None:
        self.fail_models = fail_models
        self.tried: list[str | None] = []

    async def chat(self, messages, *, model=None, **kwargs):
        self.tried.append(model)
        if model in self.fail_models:
            raise LLMRateLimitError("429")
        return Completion(text="{}", model=model or "default")

    async def vision(self, images, prompt, **kwargs):
        return await self.chat([], model=kwargs.get("model"))

    async def transcribe(self, audio, mime="audio/ogg", **kwargs):
        return "ok"

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_rate_limited_model_hands_over_to_the_next_free_one():
    client = _Flaky(fail_models={None, "free/a"})
    chain = FallbackLLMClient(client, alternates=[(client, "free/a"), (client, "free/b")])
    completion = await chain.chat([])
    assert completion.model == "free/b"
    assert client.tried == [None, "free/a", "free/b"]


@pytest.mark.asyncio
async def test_an_exhausted_chain_raises_the_last_failure():
    client = _Flaky(fail_models={None, "free/a"})
    chain = FallbackLLMClient(client, alternates=[(client, "free/a")])
    with pytest.raises(LLMRateLimitError):
        await chain.chat([])


@pytest.mark.asyncio
async def test_a_non_retryable_failure_is_not_retried_elsewhere():
    class _Broken(_Flaky):
        async def chat(self, messages, *, model=None, **kwargs):
            self.tried.append(model)
            raise LLMError("bad request")

    client = _Broken(fail_models=set())
    chain = FallbackLLMClient(client, alternates=[(client, "free/a")])
    with pytest.raises(LLMError):
        await chain.chat([])
    assert client.tried == [None]


def test_no_alternates_means_no_wrapper():
    client = _Flaky(fail_models=set())
    assert build_chain(client, None, []) is client
