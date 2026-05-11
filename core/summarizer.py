"""Provider-agnostic LLM dispatcher with response cache.

Wraps any :class:`~core.llm.base.LLMProvider`. The cache is keyed by
``(transcript_hash, mode, provider, model)`` so switching provider or model
invalidates the entry (no false hits across backends).

Cache lives in process memory for the lifetime of the app — fine for the
desktop usage pattern (one process, model switches happen by user click).
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict

from .llm.base import LLMProvider, SummaryMode

logger = logging.getLogger(__name__)

_CACHE_LIMIT = 200
_CacheKey = tuple[str, str, str, str]
_cache: "OrderedDict[_CacheKey, str]" = OrderedDict()


def _build_key(
    transcript: str,
    mode: SummaryMode,
    provider: LLMProvider,
    model: str,
) -> _CacheKey:
    digest = hashlib.sha1(transcript.encode("utf-8")).hexdigest()[:16]
    return (digest, mode.value, provider.name, model)


class Summarizer:
    """Cached entrypoint to LLM post-processing."""

    @classmethod
    async def process(
        cls,
        provider: LLMProvider,
        mode: SummaryMode,
        transcript: str,
        model: str | None = None,
    ) -> str:
        if not transcript.strip():
            return ""

        chosen_model = provider.resolve_model(model)
        key = _build_key(transcript, mode, provider, chosen_model)

        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            logger.info(
                "LLM cache HIT %s/%s mode=%s",
                provider.name,
                chosen_model,
                mode.value,
            )
            return cached

        result = await provider.process(mode, transcript, model=chosen_model)
        _cache[key] = result
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
        return result

    @classmethod
    def cache_size(cls) -> int:
        """Diagnostic helper — used by tests."""
        return len(_cache)

    @classmethod
    def clear_cache(cls) -> None:
        _cache.clear()
