"""URL-from-text handler. Detects supported sources (YouTube, RuTube, VK,
Я.Диск, Google Drive, direct media URLs) and runs the same download →
convert → transcribe → deliver pipeline as the file-upload path."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from core import AudioConverter, Downloader, Settings, Transcriber, history

from ._formats import FORMATS, build_keyboard, deliver_result
from ..utils import ProgressTracker

logger = logging.getLogger(__name__)
router = Router()

URL_PATTERN = re.compile(
    r"https?://(?:www\.)?"
    r"(?:youtube\.com/watch\?v=|youtu\.be/|"
    r"rutube\.ru/video/|"
    r"vk\.com/video|"
    r"disk\.yandex\.(?:ru|com)/[di]/|"
    r"yadi\.sk/[di]/|"
    r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)|"
    r"[^\s]+\.(?:mp3|mp4|wav|m4a|ogg|webm|mkv|flac))"
    r"[^\s]*",
    re.IGNORECASE,
)

pending_urls: dict[str, str] = {}


def _format_keyboard(url_id: str):
    return build_keyboard("url", url_id)


def detect_source(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    if "rutube.ru" in url:
        return "RuTube"
    if "vk.com" in url:
        return "VK Video"
    if "disk.yandex" in url or "yadi.sk" in url:
        return "Яндекс.Диск"
    if "drive.google.com" in url:
        return "Google Drive"
    return "Прямая ссылка"


@router.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    text = message.text or ""
    match = URL_PATTERN.search(text)
    if not match:
        return

    url = match.group(0)
    source = detect_source(url)

    url_id = uuid.uuid4().hex[:8]
    pending_urls[url_id] = url

    await message.answer(
        f"🔗 Обнаружена ссылка: *{source}*\n\nВыберите формат:",
        reply_markup=_format_keyboard(url_id),
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("url:"))
async def handle_url_format(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
) -> None:
    await callback.answer()

    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, format_key, url_id = parts
    if format_key not in FORMATS:
        return

    url = pending_urls.pop(url_id, None)
    if not url:
        await callback.message.edit_text("❌ Ссылка устарела. Отправьте её ещё раз.")
        return

    label, _, _ = FORMATS[format_key]
    await callback.message.edit_text(f"✅ Формат: {label}\n\n⬇️ Скачиваю...")

    file_path = None
    wav_path = None
    try:
        file_path, source_type = await Downloader.download(url)

        await callback.message.edit_text(f"✅ Формат: {label}\n\n🎵 Конвертирую аудио...")
        wav_path = await AudioConverter.to_wav(file_path)

        progress = ProgressTracker(
            bot, callback.message.chat.id, callback.message.message_id,
        )
        await progress.update(0, "⏳ Транскрибирую")

        segments = await Transcriber.transcribe(
            wav_path,
            progress_callback=lambda p: asyncio.create_task(progress.update(p)),
        )
        await progress.finish("✅ Транскрипция готова, формирую результат...")

        try:
            history.add(
                user_id=str(callback.from_user.id),
                source_label=f"🔗 {detect_source(url)}",
                source=url,
                segments=segments,
            )
        except Exception:
            logger.exception("history.add failed (non-fatal)")

        await deliver_result(
            bot, callback.message.chat.id, segments, format_key, settings,
        )
    except Exception as e:
        logger.exception("links handler failed")
        await bot.send_message(callback.message.chat.id, f"❌ Ошибка: {e}")
    finally:
        import shutil

        if file_path:
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass
        if wav_path:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass
            # Chunker writes per-utterance WAVs next to the source — delete
            # the whole subdir so we don't leak a few hundred files per video.
            chunks_dir = wav_path.parent / f"{wav_path.stem}_short_chunks"
            if chunks_dir.exists():
                shutil.rmtree(chunks_dir, ignore_errors=True)
