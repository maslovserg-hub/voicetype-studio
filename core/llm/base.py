"""``LLMProvider`` abstract base + helpers shared by concrete providers.

Three of the four providers (Perplexity, OpenAI, plus anything else that
respects ``/v1/chat/completions``) share the same wire format. We capture
that in :class:`_OpenAIChatProvider`. Anthropic and Gemini get their own
``process`` implementations because their request shape differs enough that
forcing them through the same path would just hide bugs.

Providers don't pull credentials from the environment. The settings UI passes
them in explicitly — keeps tests deterministic and avoids any "where did this
key come from" surprises in production.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar

import aiohttp

logger = logging.getLogger(__name__)


class SummaryMode(Enum):
    BRIEF = "brief"
    STRUCTURED = "structured"
    ROLES = "roles"
    QUESTIONS = "questions"


# Modes whose prompt expects a transcript WITH timestamps (lines like
# "[00:01:23] фраза"). Other modes get plain text.
TIMESTAMPED_MODES = {SummaryMode.QUESTIONS}


class LLMProvider(ABC):
    """Common interface for transcript post-processing backends."""

    name: ClassVar[str] = ""  # short identifier — also the cache-key segment
    default_model: ClassVar[str] = ""
    available_models: ClassVar[tuple[str, ...]] = ()

    REQUEST_TIMEOUT_S: ClassVar[int] = 180

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip() or self.default_model

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def resolve_model(self, override: str | None) -> str:
        return (override or "").strip() or self.model or self.default_model

    def _require_key(self) -> None:
        if not self.is_configured():
            raise RuntimeError(
                f"{self.name}: API-ключ не задан — проверь настройки."
            )

    @abstractmethod
    async def process(
        self,
        mode: SummaryMode,
        transcript: str,
        model: str | None = None,
    ) -> str:
        """Return the assistant text for a given mode + transcript."""


class _OpenAIChatProvider(LLMProvider):
    """Shared logic for any backend exposing ``/v1/chat/completions``.

    Subclasses set :attr:`api_url`, :attr:`name`, :attr:`default_model`,
    :attr:`available_models`, and (rarely) override :meth:`_extra_headers` if
    the service uses something other than ``Authorization: Bearer ...``.
    """

    api_url: ClassVar[str] = ""

    def _extra_headers(self) -> dict[str, str]:
        return {}

    async def process(
        self,
        mode: SummaryMode,
        transcript: str,
        model: str | None = None,
    ) -> str:
        # Local imports keep the module light and break a potential cycle
        # (prompts imports SummaryMode from here).
        from .prompts import SYSTEM_PROMPT, build_user_prompt

        self._require_key()
        if not transcript.strip():
            return ""

        chosen_model = self.resolve_model(model)
        payload = {
            "model": chosen_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(mode, transcript)},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self._extra_headers(),
        }

        logger.info(
            "%s request mode=%s model=%s transcript_chars=%d",
            self.name,
            mode.value,
            chosen_model,
            len(transcript),
        )

        timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.api_url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("%s API %s: %s", self.name, resp.status, body[:500])
                    raise RuntimeError(
                        f"{self.name} API вернул {resp.status}: {body[:200]}"
                    )
                data = await resp.json()

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            logger.error("Unexpected %s response shape: %s", self.name, data)
            raise RuntimeError(
                f"Не удалось разобрать ответ {self.name}: {exc}"
            ) from exc


# Filled in by each provider module on import. Used by ``make_provider``.
KNOWN_PROVIDERS: dict[str, type[LLMProvider]] = {}
