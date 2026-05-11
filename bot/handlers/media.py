"""Handlers for media uploads (voice, audio, video, video_note, document).

User uploads → register a short id pointing at the Telegram file_id →
present the format keyboard. When the user picks a format, download the
file, run ASR through ``core.Transcriber`` (which routes through the
shared ``asr_executor`` via ``Transcriber.set_executor`` in ``main.py``),
then dispatch via :func:`deliver_result`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from core import AudioConverter, Settings, Transcriber, config, history

from ._formats import FORMATS, build_keyboard, deliver_result
from ..utils import ProgressTracker

logger = logging.getLogger(__name__)
router = Router()


# Telegram file_ids are ~70-80 chars and ``callback_data`` is capped at
# 64 bytes. We keep a short id → file_id map and only ship the short id.
pending_files: dict[str, str] = {}


def _register_file(file_id: str) -> str:
    short_id = uuid.uuid4().hex[:8]
    pending_files[short_id] = file_id
    return short_id


def _format_keyboard(short_id: str):
    return build_keyboard("fmt", short_id)


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    short_id = _register_file(message.voice.file_id)
    await message.answer(
        "🎤 Голосовое сообщение получено. Выберите формат:",
        reply_markup=_format_keyboard(short_id),
    )


@router.message(F.audio)
async def handle_audio(message: Message, bot: Bot) -> None:
    short_id = _register_file(message.audio.file_id)
    filename = message.audio.file_name or "audio"
    await message.answer(
        f"🎵 Аудиофайл *{filename}* получен. Выберите формат:",
        reply_markup=_format_keyboard(short_id),
        parse_mode="Markdown",
    )


@router.message(F.video)
async def handle_video(message: Message, bot: Bot) -> None:
    short_id = _register_file(message.video.file_id)
    await message.answer(
        "🎬 Видеофайл получен. Выберите формат:",
        reply_markup=_format_keyboard(short_id),
    )


@router.message(F.video_note)
async def handle_video_note(message: Message, bot: Bot) -> None:
    short_id = _register_file(message.video_note.file_id)
    await message.answer(
        "⭕ Видеосообщение получено. Выберите формат:",
        reply_markup=_format_keyboard(short_id),
    )


@router.message(F.document)
async def handle_document(message: Message, bot: Bot) -> None:
    doc = message.document
    if not doc.mime_type:
        return
    if not (doc.mime_type.startswith("audio/") or doc.mime_type.startswith("video/")):
        await message.answer("⚠️ Пожалуйста, отправьте аудио или видеофайл.")
        return

    short_id = _register_file(doc.file_id)
    await message.answer(
        f"📎 Файл *{doc.file_name}* получен. Выберите формат:",
        reply_markup=_format_keyboard(short_id),
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("fmt:"))
async def handle_format_selection(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
) -> None:
    await callback.answer()

    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, format_key, short_id = parts
    if format_key not in FORMATS:
        return

    file_id = pending_files.pop(short_id, None)
    if not file_id:
        await callback.message.edit_text("❌ Сообщение устарело. Отправьте файл ещё раз.")
        return

    label, _, _ = FORMATS[format_key]
    await callback.message.edit_text(f"✅ Формат: {label}\n\n⬇️ Скачиваю файл...")

    local_path: Path | None = None
    wav_path: Path | None = None
    try:
        file = await bot.get_file(file_id)
        if not file.file_path:
            await callback.message.edit_text("❌ Не удалось получить файл.")
            return

        local_path = config.temp_dir / f"{uuid.uuid4()}{Path(file.file_path).suffix}"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        await bot.download_file(file.file_path, local_path)

        await callback.message.edit_text(f"✅ Формат: {label}\n\n🎵 Конвертирую аудио...")
        wav_path = await AudioConverter.to_wav(local_path)

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
                source_label=f"📎 {file.file_path.split('/')[-1]}",
                source=file_id,
                segments=segments,
            )
        except Exception:
            logger.exception("history.add failed (non-fatal)")

        await deliver_result(
            bot, callback.message.chat.id, segments, format_key, settings,
        )
    except Exception as e:
        logger.exception("media handler failed")
        await bot.send_message(callback.message.chat.id, f"❌ Ошибка: {e}")
    finally:
        import shutil

        if local_path:
            local_path.unlink(missing_ok=True)
        if wav_path:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass
            chunks_dir = wav_path.parent / f"{wav_path.stem}_short_chunks"
            if chunks_dir.exists():
                shutil.rmtree(chunks_dir, ignore_errors=True)
