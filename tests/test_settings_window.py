"""Tests for ``desktop.settings_window`` — pure helpers + token validation.

The Tk widget code is not exercised here. We rely on the form helpers being
extracted to module-level so each one is a one-liner to test.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest

from desktop.settings_window import (
    PROVIDER_DISPLAY,
    PROVIDER_KEYS,
    TTS_SPEAKERS,
    display_to_provider_key,
    format_whitelist_ids,
    parse_whitelist_ids,
    provider_key_to_display,
    validate_telegram_token,
)


# --- structural ---------------------------------------------------------


def test_known_providers_match_llm_module() -> None:
    """Settings UI options must stay in sync with ``core.llm`` registry."""
    from core.llm.base import KNOWN_PROVIDERS

    # Allow the test fake-provider — strip any non-built-in keys.
    real = {k for k in KNOWN_PROVIDERS if k in {"perplexity", "openai", "anthropic", "gemini"}}
    assert set(PROVIDER_KEYS) == real


def test_tts_speakers_match_spec() -> None:
    """Per FR-9 — exactly five silero v4_ru voices."""
    assert set(TTS_SPEAKERS) == {"aidar", "baya", "kseniya", "xenia", "eugene"}


# --- provider display ↔ key conversion ----------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("OpenAI", "openai"),
        ("openai", "openai"),
        ("  Anthropic  ", "anthropic"),
        ("PERPLEXITY", "perplexity"),
        ("Gemini", "gemini"),
    ],
)
def test_display_to_provider_key(given: str, expected: str) -> None:
    assert display_to_provider_key(given) == expected


def test_display_to_provider_key_passes_unknown_through() -> None:
    """Unknown values are lower-cased but otherwise preserved so user data
    isn't silently lost on save/reopen."""
    assert display_to_provider_key("Llama-Local") == "llama-local"


def test_provider_key_to_display_known() -> None:
    for key, label in PROVIDER_DISPLAY.items():
        assert provider_key_to_display(key) == label


def test_provider_key_to_display_unknown() -> None:
    assert provider_key_to_display("ollama") == "ollama"


# --- whitelist parsing --------------------------------------------------


def test_parse_whitelist_ids_simple() -> None:
    assert parse_whitelist_ids("12345, 67890") == [12345, 67890]


def test_parse_whitelist_ids_drops_invalid() -> None:
    assert parse_whitelist_ids("100, abc, 200, , 300") == [100, 200, 300]


def test_parse_whitelist_ids_handles_semicolons() -> None:
    assert parse_whitelist_ids("100; 200;300") == [100, 200, 300]


def test_parse_whitelist_ids_empty() -> None:
    assert parse_whitelist_ids("") == []
    assert parse_whitelist_ids(None) == []  # type: ignore[arg-type]
    assert parse_whitelist_ids("   ") == []


def test_format_whitelist_ids() -> None:
    assert format_whitelist_ids([100, 200, 300]) == "100, 200, 300"
    assert format_whitelist_ids([]) == ""
    assert format_whitelist_ids(None) == ""  # type: ignore[arg-type]


def test_whitelist_roundtrip() -> None:
    """parse(format(x)) == x for any list of ints."""
    original = [12345, 67890, 100500]
    assert parse_whitelist_ids(format_whitelist_ids(original)) == original


# --- token validator ----------------------------------------------------


def test_token_validator_rejects_empty() -> None:
    ok, msg = asyncio.run(validate_telegram_token(""))
    assert not ok
    assert "пуст" in msg.lower()


def test_token_validator_rejects_whitespace_only() -> None:
    ok, msg = asyncio.run(validate_telegram_token("   "))
    assert not ok


def test_token_validator_handles_network_error() -> None:
    """If aiohttp blows up, we surface a friendly message rather than crashing."""

    class _BoomSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, *a, **kw):
            raise OSError("dns fail")

    with patch("aiohttp.ClientSession", _BoomSession):
        ok, msg = asyncio.run(validate_telegram_token("123:fake"))
    assert not ok
    assert "ошибка" in msg.lower()


@pytest.mark.live
@pytest.mark.skipif(
    not os.getenv("TG_BOT_TOKEN"),
    reason="TG_BOT_TOKEN not set — skipping live Telegram getMe",
)
def test_token_validator_live() -> None:
    """Hits Telegram for real if a token is in env."""
    ok, msg = asyncio.run(validate_telegram_token(os.environ["TG_BOT_TOKEN"]))
    assert ok, f"validate_telegram_token said no: {msg}"
    assert "@" in msg
