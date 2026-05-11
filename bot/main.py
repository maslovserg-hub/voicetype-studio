"""Telegram bot polling — driven from ``main.App._start_bot``.

Lives on the asyncio loop running in main.py's daemon thread. Two entry
points:

* :func:`start_bot_polling` — constructs Bot/Dispatcher, registers the
  whitelist middleware + main router, calls ``delete_webhook`` to take
  ownership of pending updates, then ``start_polling(handle_signals=False)``
  (mandatory: aiogram tries to register SIGINT on the main thread, which
  we are not).
* :func:`stop_bot_polling` — flips an internal flag and closes the bot
  session so the polling loop exits cleanly.

We keep ``main.py``-shaped state at module level (one bot per process)
because the embedding pattern is "settings flips the switch, polling
starts/stops" — we never need a second concurrent bot.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from core import Settings

from .handlers import main_router
from .middleware import WhitelistMiddleware

logger = logging.getLogger(__name__)


BOT_COMMANDS = [
    BotCommand(command="start", description="🚀 Начало работы"),
    BotCommand(command="history", description="📚 Последние транскрипции"),
    BotCommand(command="help", description="❔ Справка"),
    BotCommand(command="status", description="📊 Статус обработки"),
]


# Module-level state — one bot per process.
_bot: Optional[Bot] = None
_dp: Optional[Dispatcher] = None
_polling_task: Optional[asyncio.Task] = None
_running: bool = False


async def start_bot_polling(
    *,
    settings: Settings,
    asr_executor: Any,
    gui_queue: Any = None,
) -> None:
    """Start aiogram polling on the current event loop.

    Returns once the polling task has been *scheduled* — the actual loop
    keeps running in the background until :func:`stop_bot_polling`.
    """
    global _bot, _dp, _polling_task, _running

    if _running:
        logger.info("bot.main.start_bot_polling: already running")
        return

    token = (settings.bot_token or "").strip()
    if not token:
        logger.warning("bot.main: empty bot_token — skipping start")
        return

    _bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    _dp = Dispatcher()

    # Whitelist middleware — captures the current snapshot of allowed ids.
    _dp.message.middleware(WhitelistMiddleware(settings.whitelist_ids))

    _dp.include_router(main_router)

    # Workflow data — handlers can declare these as kwargs.
    _dp["settings"] = settings
    _dp["asr_executor"] = asr_executor
    if gui_queue is not None:
        _dp["gui_queue"] = gui_queue

    try:
        await _bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        logger.exception("delete_webhook failed (continuing anyway)")

    try:
        await _bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        logger.exception("set_my_commands failed (continuing anyway)")

    # ``handle_signals=False`` is mandatory off the main thread — aiogram
    # would otherwise crash trying to register SIGINT.
    _polling_task = asyncio.create_task(
        _dp.start_polling(_bot, handle_signals=False),
        name="aiogram-polling",
    )
    _running = True
    logger.info(
        "Telegram bot started (whitelist=%s, default_provider=%s)",
        sorted(settings.whitelist_ids),
        settings.default_provider,
    )


async def stop_bot_polling() -> None:
    """Cleanly stop polling and close the bot session."""
    global _bot, _dp, _polling_task, _running

    if not _running:
        return

    _running = False

    if _dp is not None:
        try:
            await _dp.stop_polling()
        except Exception:
            logger.exception("dp.stop_polling failed")

    if _polling_task is not None and not _polling_task.done():
        try:
            await asyncio.wait_for(_polling_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            _polling_task.cancel()

    if _bot is not None:
        try:
            await _bot.session.close()
        except Exception:
            logger.exception("bot.session.close failed")

    _bot = None
    _dp = None
    _polling_task = None
    logger.info("Telegram bot stopped")


def is_running() -> bool:
    return _running
