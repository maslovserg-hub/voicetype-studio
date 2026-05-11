"""Edit-in-place progress messages for long-running Telegram tasks.

Copied verbatim from ``transcription-bot/bot/utils/progress.py`` — no Studio-
specific changes. The class-level lock on ``finish`` is load-bearing: it
prevents an in-flight ``update(100)`` task from overwriting the final result
message after the success path has already published it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot

logger = logging.getLogger(__name__)


class ProgressTracker:
    def __init__(self, bot: Bot, chat_id: int, message_id: Optional[int] = None):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self._last_percent = -1
        self._finished = False
        self._lock = asyncio.Lock()

    async def start(self, text: str = "⏳ Обрабатываю... 0%") -> None:
        msg = await self.bot.send_message(self.chat_id, text)
        self.message_id = msg.message_id
        self._last_percent = 0

    async def update(self, percent: int, prefix: str = "⏳ Обрабатываю") -> None:
        if self.message_id is None or self._finished:
            return

        # Coalesce to 10% steps — Telegram rate-limits edit_message_text and
        # we don't want to burn that budget on cosmetic 1% bumps.
        step = (percent // 10) * 10
        if step <= self._last_percent:
            return

        async with self._lock:
            if self._finished or step <= self._last_percent:
                return
            self._last_percent = step
            try:
                await self.bot.edit_message_text(
                    f"{prefix}... {step}%",
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                )
            except Exception as exc:
                logger.warning("progress.update edit failed: %s", exc)

    async def finish(self, text: str) -> None:
        async with self._lock:
            self._finished = True
            if self.message_id is not None:
                try:
                    await self.bot.edit_message_text(
                        text, chat_id=self.chat_id, message_id=self.message_id,
                    )
                    return
                except Exception as exc:
                    logger.warning(
                        "progress.finish edit failed (%s); falling back to send",
                        exc,
                    )
            try:
                await self.bot.send_message(self.chat_id, text)
            except Exception as exc:
                logger.error("progress.finish send also failed: %s", exc)

    async def error(self, text: str = "❌ Ошибка обработки") -> None:
        await self.finish(text)
