"""Smoke tests for the multi-provider LLM layer.

* No network calls except in the optional ``test_perplexity_live`` guarded
  by the ``PPLX_API_KEY`` env var.
* Cache + dispatch logic exercised through a ``FakeProvider`` so we can
  count how many times the underlying ``process`` actually ran.
"""

from __future__ import annotations

import asyncio
import os
from typing import ClassVar

import pytest

from core import Summarizer, SummaryMode, make_provider
from core.llm import (
    AnthropicProvider,
    GeminiProvider,
    KNOWN_PROVIDERS,
    LLMProvider,
    OpenAIProvider,
    PerplexityProvider,
)


# ---------- imports / factory ----------


def test_provider_imports() -> None:
    for cls in (
        PerplexityProvider,
        OpenAIProvider,
        AnthropicProvider,
        GeminiProvider,
    ):
        assert issubclass(cls, LLMProvider)
        assert cls.name
        assert cls.default_model in cls.available_models


def test_known_providers_complete() -> None:
    assert set(KNOWN_PROVIDERS) == {"perplexity", "openai", "anthropic", "gemini"}


@pytest.mark.parametrize(
    "name,cls",
    [
        ("perplexity", PerplexityProvider),
        ("OpenAI", OpenAIProvider),
        ("  Anthropic  ", AnthropicProvider),
        ("gemini", GeminiProvider),
    ],
)
def test_make_provider_resolves(name: str, cls: type[LLMProvider]) -> None:
    p = make_provider(name, api_key="test")
    assert isinstance(p, cls)
    assert p.is_configured()
    assert p.model == p.default_model


def test_make_provider_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        make_provider("ollama", api_key="x")


def test_unconfigured_provider_raises() -> None:
    p = OpenAIProvider(api_key="")
    assert not p.is_configured()
    with pytest.raises(RuntimeError, match="API-ключ не задан"):
        asyncio.run(p.process(SummaryMode.BRIEF, "hi"))


def test_resolve_model_override() -> None:
    p = OpenAIProvider(api_key="k")
    assert p.resolve_model(None) == "gpt-4o-mini"
    assert p.resolve_model("") == "gpt-4o-mini"
    assert p.resolve_model("gpt-4o") == "gpt-4o"


# ---------- summarizer cache ----------


class FakeProvider(LLMProvider):
    """Records call count + last seen mode/model — no network."""

    name: ClassVar[str] = "fake"
    default_model: ClassVar[str] = "fake-1"
    available_models: ClassVar[tuple[str, ...]] = ("fake-1", "fake-2")

    def __init__(self, api_key: str = "k", model: str | None = None) -> None:
        super().__init__(api_key, model)
        self.calls = 0
        self.last_mode: SummaryMode | None = None
        self.last_model: str | None = None

    async def process(self, mode, transcript, model=None):
        self.calls += 1
        self.last_mode = mode
        self.last_model = self.resolve_model(model)
        return f"answer:{mode.value}:{self.last_model}:{len(transcript)}"


@pytest.fixture(autouse=True)
def _clear_cache():
    Summarizer.clear_cache()
    yield
    Summarizer.clear_cache()


def test_summarizer_caches_same_inputs() -> None:
    p = FakeProvider()
    text = "это тестовая транскрипция для саммари"

    a = asyncio.run(Summarizer.process(p, SummaryMode.BRIEF, text))
    b = asyncio.run(Summarizer.process(p, SummaryMode.BRIEF, text))
    assert a == b
    assert p.calls == 1, "second call must hit the cache"
    assert Summarizer.cache_size() == 1


def test_summarizer_cache_keyed_by_mode() -> None:
    p = FakeProvider()
    text = "x"
    asyncio.run(Summarizer.process(p, SummaryMode.BRIEF, text))
    asyncio.run(Summarizer.process(p, SummaryMode.STRUCTURED, text))
    assert p.calls == 2
    assert Summarizer.cache_size() == 2


def test_summarizer_cache_keyed_by_model() -> None:
    p = FakeProvider()
    text = "x"
    asyncio.run(Summarizer.process(p, SummaryMode.BRIEF, text, model="fake-1"))
    asyncio.run(Summarizer.process(p, SummaryMode.BRIEF, text, model="fake-2"))
    assert p.calls == 2, "switching model must miss cache"


def test_summarizer_cache_keyed_by_provider() -> None:
    """Two providers that happen to produce the same text must not collide."""
    p1 = FakeProvider()

    class FakeProvider2(FakeProvider):
        name = "fake2"

    p2 = FakeProvider2()
    text = "x"
    asyncio.run(Summarizer.process(p1, SummaryMode.BRIEF, text))
    asyncio.run(Summarizer.process(p2, SummaryMode.BRIEF, text))
    assert p1.calls == 1
    assert p2.calls == 1
    assert Summarizer.cache_size() == 2


def test_summarizer_empty_transcript_returns_empty() -> None:
    p = FakeProvider()
    assert asyncio.run(Summarizer.process(p, SummaryMode.BRIEF, "")) == ""
    assert asyncio.run(Summarizer.process(p, SummaryMode.BRIEF, "   ")) == ""
    assert p.calls == 0


# ---------- live test, opt-in ----------


@pytest.mark.live
@pytest.mark.skipif(
    not os.getenv("PPLX_API_KEY"),
    reason="PPLX_API_KEY not set — skipping live Perplexity call",
)
def test_perplexity_live_brief() -> None:
    p = make_provider("perplexity", api_key=os.environ["PPLX_API_KEY"])
    result = asyncio.run(
        Summarizer.process(
            p,
            SummaryMode.BRIEF,
            "Сегодня обсудили план релиза. Главное — успеть к пятнице. "
            "Маркетинг готовит баннер, разработчики чинят критический баг.",
        )
    )
    assert isinstance(result, str)
    assert len(result) > 30
