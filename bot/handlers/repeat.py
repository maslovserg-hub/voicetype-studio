"""``rpt:<format>:<short_id>`` — user wants another format/summary of an
already-transcribed file. Reuses cached segments, so neither download nor
GigaAM run again.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from core import Settings, transcript_cache

from ._formats import FORMATS, deliver_result

router = Router()


@router.callback_query(F.data.startswith("rpt:"))
async def handle_repeat(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
) -> None:
    await callback.answer()

    parts = callback.data.split(":")
    if len(parts) != 3:
        return

    _, format_key, transcript_id = parts
    if format_key not in FORMATS:
        return

    segments = transcript_cache.get(transcript_id)
    if segments is None:
        await callback.message.edit_text(
            "❌ Транскрипция этого файла уже не в кэше "
            "(бот перезапускался или прошло много времени). Отправь файл заново."
        )
        return

    label, _, _ = FORMATS[format_key]
    await callback.message.edit_text(f"⏳ Готовлю: {label}...")

    try:
        await deliver_result(
            bot, callback.message.chat.id, segments, format_key, settings,
        )
    except Exception as e:
        await bot.send_message(callback.message.chat.id, f"❌ Ошибка: {e}")
