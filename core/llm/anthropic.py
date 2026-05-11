"""Anthropic ``messages`` endpoint — Claude 3.5 family.

Notable differences from OpenAI-compatible providers:
* ``x-api-key`` instead of ``Authorization: Bearer``;
* ``anthropic-version`` header is required;
* ``system`` is a top-level field, not a message;
* ``max_tokens`` is mandatory.
"""

from __future__ import annotations

import logging
from typing import ClassVar

import aiohttp

from .base import KNOWN_PROVIDERS, LLMProvider, SummaryMode

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-3-5-haiku-latest"
    available_models = ("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest")

    api_url: ClassVar[str] = "https://api.anthropic.com/v1/messages"
    api_version: ClassVar[str] = "2023-06-01"
    max_tokens: ClassVar[int] = 4096

    async def process(
        self,
        mode: SummaryMode,
        transcript: str,
        model: str | None = None,
    ) -> str:
        from .prompts import SYSTEM_PROMPT, build_user_prompt

        self._require_key()
        if not transcript.strip():
            return ""

        chosen_model = self.resolve_model(model)
        payload = {
            "model": chosen_model,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": build_user_prompt(mode, transcript)},
            ],
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "Content-Type": "application/json",
        }

        logger.info(
            "anthropic request mode=%s model=%s transcript_chars=%d",
            mode.value,
            chosen_model,
            len(transcript),
        )

        timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.api_url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("anthropic API %s: %s", resp.status, body[:500])
                    raise RuntimeError(
                        f"Anthropic API вернул {resp.status}: {body[:200]}"
                    )
                data = await resp.json()

        # Anthropic returns ``content: [{type: 'text', text: '...'}, ...]``.
        try:
            blocks = data["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return text.strip()
        except (KeyError, TypeError, AttributeError) as exc:
            logger.error("Unexpected anthropic response shape: %s", data)
            raise RuntimeError(f"Не удалось разобрать ответ Anthropic: {exc}") from exc


KNOWN_PROVIDERS["anthropic"] = AnthropicProvider
