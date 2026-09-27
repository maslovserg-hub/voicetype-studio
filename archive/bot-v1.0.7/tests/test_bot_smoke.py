"""Smoke tests for the migrated Telegram bot.

We don't open a network connection — all tests stay at the import +
structural level + middleware unit tests + dispatch helpers. A live test
of the full polling flow would need a real Bot Token in env.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

from core import Settings


# ---------- import smoke -------------------------------------------------


def test_bot_modules_import() -> None:
    import bot
    import bot.handlers
    import bot.handlers._formats as formats_mod
    import bot.handlers.links as links_mod
    import bot.handlers.media as media_mod
    import bot.handlers.repeat as repeat_mod
    import bot.handlers.start as start_mod
    import bot.main as main_mod
    import bot.middleware
    import bot.utils

    assert main_mod.start_bot_polling is not None
    assert main_mod.stop_bot_polling is not None
    assert hasattr(bot.handlers, "main_router")
    for mod in (start_mod, links_mod, media_mod, repeat_mod):
        assert hasattr(mod, "router"), f"{mod.__name__} missing router"
    assert formats_mod.FORMATS
    assert formats_mod.deliver_result is not None


def test_no_residual_old_bot_imports() -> None:
    """No leftover ``from bot.config`` or ``from bot.services`` (old layout)."""
    import sys

    import bot  # noqa: F401
    from bot import handlers, main, middleware, utils  # noqa: F401

    for name, module in list(sys.modules.items()):
        if not name.startswith("bot"):
            continue
        src = getattr(module, "__file__", None)
        if not src:
            continue
        with open(src, "r", encoding="utf-8") as f:
            text = f.read()
        for forbidden in ("from bot.config", "from bot.services"):
            assert forbidden not in text, (
                f"{name} still references {forbidden!r}"
            )


# ---------- format keyboard ----------------------------------------------


def test_formats_dict_complete() -> None:
    from bot.handlers._formats import FORMATS

    expected = {"text", "ts", "srt", "brief", "struct", "roles", "ques", "tts"}
    assert set(FORMATS) == expected


def test_build_keyboard_callback_data_under_64_bytes() -> None:
    """Telegram's hard limit on inline button callback_data."""
    from bot.handlers._formats import build_keyboard

    kb = build_keyboard("rpt", "abcd1234")  # max realistic short id (8 hex)
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64, btn.callback_data


def test_keyboard_layout_has_three_rows_and_eight_buttons() -> None:
    from bot.handlers._formats import build_keyboard

    kb = build_keyboard("fmt", "x")
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 8
    assert len(kb.inline_keyboard) == 3


# ---------- whitelist middleware -----------------------------------------


def test_whitelist_blocks_unknown_user() -> None:
    from bot.middleware import WhitelistMiddleware

    mw = WhitelistMiddleware([100, 200])

    handler = AsyncMock()
    event = AsyncMock()
    event.from_user = AsyncMock()
    event.from_user.id = 999
    # ``isinstance`` check inside the middleware uses Message; fake it.
    from aiogram.types import Message

    event.__class__ = Message  # type: ignore[assignment]

    asyncio.run(mw(handler, event, {}))
    handler.assert_not_called()
    event.answer.assert_awaited()


def test_whitelist_passes_known_user() -> None:
    from bot.middleware import WhitelistMiddleware

    mw = WhitelistMiddleware([100, 200])

    handler = AsyncMock(return_value="OK")
    event = AsyncMock()
    event.from_user = AsyncMock()
    event.from_user.id = 100
    from aiogram.types import Message

    event.__class__ = Message  # type: ignore[assignment]

    result = asyncio.run(mw(handler, event, {}))
    handler.assert_awaited_once()
    assert result == "OK"


def test_whitelist_empty_blocks_everyone() -> None:
    """An unconfigured whitelist closes the bot — never opens it."""
    from bot.middleware import WhitelistMiddleware

    mw = WhitelistMiddleware([])
    handler = AsyncMock()
    event = AsyncMock()
    event.from_user = AsyncMock()
    event.from_user.id = 1
    from aiogram.types import Message

    event.__class__ = Message  # type: ignore[assignment]

    asyncio.run(mw(handler, event, {}))
    handler.assert_not_called()


def test_whitelist_passes_non_message_events() -> None:
    """Callback queries / inline queries don't go through the message gate."""
    from bot.middleware import WhitelistMiddleware

    mw = WhitelistMiddleware([])
    handler = AsyncMock(return_value="OK")
    fake_callback = object()  # not a Message instance

    result = asyncio.run(mw(handler, fake_callback, {}))
    handler.assert_awaited_once()
    assert result == "OK"


# ---------- bot.main lifecycle (no real polling) -------------------------


def test_start_signature_includes_executor_kwarg() -> None:
    """``main.App._start_bot`` calls with ``settings=`` and ``asr_executor=``."""
    from bot.main import start_bot_polling

    assert inspect.iscoroutinefunction(start_bot_polling)
    sig = inspect.signature(start_bot_polling)
    assert "settings" in sig.parameters
    assert "asr_executor" in sig.parameters


def test_start_skips_when_token_blank() -> None:
    from bot import main as bot_main

    bot_main._running = False
    settings = Settings(bot_enabled=True, bot_token="   ")
    asyncio.run(bot_main.start_bot_polling(settings=settings, asr_executor=None))
    assert bot_main.is_running() is False


def test_handlers_declare_settings_kwarg() -> None:
    """media/links/repeat callback handlers must take ``settings`` so the
    workflow_data injection in bot.main works."""
    from bot.handlers.links import handle_url_format
    from bot.handlers.media import handle_format_selection
    from bot.handlers.repeat import handle_repeat

    for fn in (handle_format_selection, handle_url_format, handle_repeat):
        params = inspect.signature(fn).parameters
        assert "settings" in params, f"{fn.__name__} missing settings kwarg"


# ---------- deliver_result error paths ----------------------------------


def test_deliver_result_no_api_key_sends_friendly_error() -> None:
    """When the user picks a summary mode but settings.api_keys is empty,
    we surface the RuntimeError as a chat message rather than crashing."""
    from bot.handlers._formats import deliver_result
    from core import Segment

    settings = Settings(default_provider="openai", api_keys={})
    bot = AsyncMock()
    segments = [Segment(start=0, end=1, text="привет")]

    asyncio.run(deliver_result(bot, 12345, segments, "brief", settings))
    # Friendly error must reach the chat.
    bot.send_message.assert_any_await(12345, mocker_any_starts_with("❌"))


def mocker_any_starts_with(prefix: str):
    """Helper: matcher used inside ``assert_any_await``."""
    class _Match:
        def __eq__(self, other):
            return isinstance(other, str) and other.startswith(prefix)

        def __repr__(self):
            return f"<starts_with {prefix!r}>"

    return _Match()
