"""Regression — when settings change at runtime, the bot must see them.

Earlier ``App._on_settings_saved`` only restarted the bot when ``bot_token``
changed. Adding a Telegram ID to the whitelist while the bot was running
silently did nothing — the WhitelistMiddleware had cached the old set at
construction time, so even after Save the user kept getting "⛔ ваш ID не
в whitelist". This test pins the contract: a whitelist edit triggers a
stop+start cycle.

We don't actually run the bot — we replace ``_start_bot`` / ``_stop_bot``
on a fake ``App``-shaped object and just check the call sequence.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from core import Settings


def _build_fake_app(starting_settings: Settings):
    """Pull just ``_on_settings_saved`` off the real App class onto a
    MagicMock-shaped instance so we can call it without standing up Tk +
    asyncio + tray. The method is plain attribute access, so unbinding it
    via ``__func__`` and re-binding to the mock is enough."""
    import main as main_mod

    fake = types.SimpleNamespace()
    fake.settings = starting_settings
    fake._transcriptor = None
    fake._start_bot = MagicMock()
    fake._stop_bot = MagicMock()

    # Rebind the unbound method to our fake. settings_io.save also gets
    # patched by the test so disk isn't touched.
    fake._on_settings_saved = main_mod.App._on_settings_saved.__get__(fake)
    return fake


@pytest.fixture
def patch_settings_save(monkeypatch):
    """settings_io.save is invoked unconditionally inside _on_settings_saved.
    No-op it so tests don't write to %APPDATA%."""
    import main as main_mod

    monkeypatch.setattr(main_mod.settings_io, "save", lambda *a, **kw: None)


# ---- whitelist-only change must restart bot --------------------------


def test_whitelist_change_triggers_restart(patch_settings_save) -> None:
    old = Settings(bot_enabled=True, bot_token="123:abc", whitelist_ids=[100])
    new = Settings(bot_enabled=True, bot_token="123:abc", whitelist_ids=[100, 200])
    fake = _build_fake_app(old)

    fake._on_settings_saved(new)

    fake._stop_bot.assert_called_once()
    fake._start_bot.assert_called_once()


def test_whitelist_unchanged_no_restart(patch_settings_save) -> None:
    """Saving with no relevant changes must not stop+start the bot —
    that would be a noticeable polling hiccup for the user."""
    s = Settings(
        bot_enabled=True,
        bot_token="123:abc",
        whitelist_ids=[100, 200],
        default_provider="perplexity",
    )
    fake = _build_fake_app(s)

    # Same settings (different object).
    fake._on_settings_saved(Settings(
        bot_enabled=True,
        bot_token="123:abc",
        whitelist_ids=[100, 200],
        default_provider="perplexity",
    ))

    fake._stop_bot.assert_not_called()
    fake._start_bot.assert_not_called()


def test_whitelist_reorder_does_not_restart(patch_settings_save) -> None:
    """``[100, 200]`` and ``[200, 100]`` are the same whitelist."""
    old = Settings(bot_enabled=True, bot_token="t", whitelist_ids=[100, 200])
    new = Settings(bot_enabled=True, bot_token="t", whitelist_ids=[200, 100])
    fake = _build_fake_app(old)

    fake._on_settings_saved(new)

    fake._stop_bot.assert_not_called()
    fake._start_bot.assert_not_called()


def test_token_change_still_triggers_restart(patch_settings_save) -> None:
    """Pre-existing branch — make sure it didn't regress."""
    old = Settings(bot_enabled=True, bot_token="old", whitelist_ids=[100])
    new = Settings(bot_enabled=True, bot_token="new", whitelist_ids=[100])
    fake = _build_fake_app(old)

    fake._on_settings_saved(new)

    fake._stop_bot.assert_called_once()
    fake._start_bot.assert_called_once()


def test_enable_bot_starts(patch_settings_save) -> None:
    old = Settings(bot_enabled=False, bot_token="t", whitelist_ids=[100])
    new = Settings(bot_enabled=True, bot_token="t", whitelist_ids=[100])
    fake = _build_fake_app(old)

    fake._on_settings_saved(new)

    fake._stop_bot.assert_not_called()
    fake._start_bot.assert_called_once()


def test_disable_bot_stops(patch_settings_save) -> None:
    old = Settings(bot_enabled=True, bot_token="t", whitelist_ids=[100])
    new = Settings(bot_enabled=False, bot_token="t", whitelist_ids=[100])
    fake = _build_fake_app(old)

    fake._on_settings_saved(new)

    fake._stop_bot.assert_called_once()
    fake._start_bot.assert_not_called()


def test_provider_change_no_restart(patch_settings_save) -> None:
    """``default_provider`` flows through workflow_data — picked up on next
    handler call, no restart needed."""
    old = Settings(
        bot_enabled=True, bot_token="t", whitelist_ids=[100],
        default_provider="perplexity",
    )
    new = Settings(
        bot_enabled=True, bot_token="t", whitelist_ids=[100],
        default_provider="openai",
    )
    fake = _build_fake_app(old)

    fake._on_settings_saved(new)

    fake._stop_bot.assert_not_called()
    fake._start_bot.assert_not_called()
