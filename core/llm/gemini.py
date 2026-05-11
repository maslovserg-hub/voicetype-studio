"""Google Gemini ``generateContent`` endpoint.

Notable differences from the OpenAI shape:
* auth via ``?key=...`` query param (no Authorization header);
* per-model URL: ``/v1beta/models/{model}:generateContent``;
* request body uses ``contents: [{role, parts: [{text}]}]``;
* the system prompt goes into ``systemInstruction`` (also a ``parts`` shape).
"""

from __future__ import annotations

import logging
from typing import ClassVar

import aiohttp

from .base import KNOWN_PROVIDERS, LLMProvider, SummaryMode

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    name = "gemini"
    default_model = "gemini-1.5-flash"
    available_models = ("gemini-1.5-flash", "gemini-1.5-pro")

    api_base: ClassVar[str] = (
        "https://generativelanguage.googleapis.com/v1beta/models"
    )

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
        url = f"{self.api_base}/{chosen_model}:generateContent"
        params = {"key": self.api_key}

        payload = {
            "systemInstruction": {
                "parts": [{"text": SYSTEM_PROMPT}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": build_user_prompt(mode, transcript)}],
                },
            ],
            "generationConfig": {
                "temperature": 0.2,
            },
        }
        headers = {"Content-Type": "application/json"}

        logger.info(
            "gemini request mode=%s model=%s transcript_chars=%d",
            mode.value,
            chosen_model,
            len(transcript),
        )

        timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, headers=headers, params=params, json=payload
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("gemini API %s: %s", resp.status, body[:500])
                    raise RuntimeError(
                        f"Gemini API вернул {resp.status}: {body[:200]}"
                    )
                data = await resp.json()

        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
            return text.strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            logger.error("Unexpected gemini response shape: %s", data)
            raise RuntimeError(f"Не удалось разобрать ответ Gemini: {exc}") from exc


KNOWN_PROVIDERS["gemini"] = GeminiProvider
