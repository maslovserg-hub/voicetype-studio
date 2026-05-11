"""Perplexity sonar models — OpenAI-compatible chat/completions endpoint.

Sonar models always have web-search enabled at the API level (no flag turns
it off). We rely on the system prompt in :mod:`core.llm.prompts` to keep the
model focused on the supplied transcript.
"""

from __future__ import annotations

from .base import KNOWN_PROVIDERS, _OpenAIChatProvider


class PerplexityProvider(_OpenAIChatProvider):
    name = "perplexity"
    api_url = "https://api.perplexity.ai/chat/completions"
    default_model = "sonar"
    available_models = ("sonar", "sonar-pro")


KNOWN_PROVIDERS["perplexity"] = PerplexityProvider
