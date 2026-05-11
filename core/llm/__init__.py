"""Multi-provider LLM layer for transcript post-processing.

The :class:`LLMProvider` ABC unifies four backends (Perplexity, OpenAI,
Anthropic, Google Gemini). The :class:`~core.summarizer.Summarizer` adds an
in-memory cache on top, keyed by ``(transcript_hash, mode, provider, model)``
so switching provider doesn't accidentally serve a stale answer.

Use :func:`make_provider` to construct a provider from settings — it accepts
the same lower-case names used by ``settings.json``.
"""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import (
    KNOWN_PROVIDERS,
    LLMProvider,
    SummaryMode,
    TIMESTAMPED_MODES,
)
from .gemini import GeminiProvider
from .openai import OpenAIProvider
from .perplexity import PerplexityProvider


def make_provider(
    name: str,
    api_key: str,
    model: str | None = None,
) -> LLMProvider:
    """Construct a provider by short name (``perplexity``, ``openai``, ...).

    Lower-cased, whitespace-stripped. Raises :class:`ValueError` for unknown
    names. The provider is *not* validated here — call ``is_configured()`` /
    ``process()`` to surface missing keys.
    """
    key = (name or "").strip().lower()
    cls = KNOWN_PROVIDERS.get(key)
    if cls is None:
        valid = ", ".join(sorted(KNOWN_PROVIDERS))
        raise ValueError(f"Unknown LLM provider {name!r}. Known: {valid}")
    return cls(api_key=api_key, model=model)


__all__ = [
    "AnthropicProvider",
    "GeminiProvider",
    "KNOWN_PROVIDERS",
    "LLMProvider",
    "OpenAIProvider",
    "PerplexityProvider",
    "SummaryMode",
    "TIMESTAMPED_MODES",
    "make_provider",
]
