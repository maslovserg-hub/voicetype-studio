"""Reject every Message whose sender isn't in the configured whitelist.

The whitelist now lives in :class:`core.Settings.whitelist_ids` (was: env-
driven ``config.WHITELIST_IDS`` in transcription-bot). The middleware is
constructed once with a snapshot — settings changes that should rotate the
whitelist trigger a bot restart from ``main.App._on_settings_saved``, so
re-reading on every call would only complicate things.

If the whitelist is empty, the bot is *closed* — no one gets through. We
deliberately don't fall back to "everyone allowed" because surprise public
access on an unconfigured token is the kind of thing a careless save in
the settings UI would otherwise enable.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Iterable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class WhitelistMiddleware(BaseMiddleware):
    def __init__(self, whitelist_ids: Iterable[int]) -> None:
        self._allowed: frozenset[int] = frozenset(int(i) for i in whitelist_ids or [])

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None

        if user_id is None or user_id not in self._allowed:
            await event.answer(
                "⛔ Извините, бот работает только для приглашённых пользователей.\n\n"
                f"Ваш ID: `{user_id}`",
                parse_mode="Markdown",
            )
            return None

        return await handler(event, data)
