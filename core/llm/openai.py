"""OpenAI chat/completions provider — gpt-4o family."""

from __future__ import annotations

from .base import KNOWN_PROVIDERS, _OpenAIChatProvider


class OpenAIProvider(_OpenAIChatProvider):
    name = "openai"
    api_url = "https://api.openai.com/v1/chat/completions"
    default_model = "gpt-4o-mini"
    available_models = ("gpt-4o", "gpt-4o-mini")


KNOWN_PROVIDERS["openai"] = OpenAIProvider
