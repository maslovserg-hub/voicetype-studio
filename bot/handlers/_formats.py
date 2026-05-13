"""Single source of truth for the 8 format options + delivery for the bot.

Both ``media.py`` and ``links.py`` (initial transcription) and ``repeat.py``
(callbacks against cached segments) build the same keyboard from this dict
and call :func:`deliver_result`.

The big change vs. transcription-bot: ``Summarizer.process`` now requires a
:class:`~core.LLMProvider` instance, so :func:`deliver_result` takes the
whole :class:`Settings` and constructs the configured provider on demand.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import List

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from core import (
    Formatter,
    OutputFormat,
    Segment,
    Settings,
    Summarizer,
    SummaryMode,
    TIMESTAMPED_MODES,
    TTSService,
    config,
    make_provider,
    transcript_cache,
)

logger = logging.getLogger(__name__)


# label, kind ∈ {"format", "summary", "tts"}, value (OutputFormat | SummaryMode | None)
FORMATS: dict[str, tuple[str, str, object]] = {
    "text":   ("📝 Текст",       "format",  OutputFormat.TEXT),
    "ts":     ("⏱ Таймкоды",    "format",  OutputFormat.TIMESTAMPS),
    "srt":    ("📺 SRT",         "format",  OutputFormat.SRT),
    "brief":  ("📋 Тезисы",      "summary", SummaryMode.BRIEF),
    "struct": ("📚 Конспект",    "summary", SummaryMode.STRUCTURED),
    "roles":  ("🎭 По ролям",    "summary", SummaryMode.ROLES),
    "ques":   ("❓ Вопросы",      "summary", SummaryMode.QUESTIONS),
    "tts":    ("🔊 Озвучка",     "tts",     None),
}

_BASE_ROW = ["text", "ts", "srt"]
_SMART_ROW = ["brief", "struct", "roles"]
_EXTRA_ROW = ["ques", "tts"]


def build_keyboard(prefix: str, payload_id: str) -> InlineKeyboardMarkup:
    """Three-row keyboard. ``prefix`` ∈ ``{"fmt", "url", "rpt"}``; ``payload_id``
    is a short id pointing at a Telegram file_id, a URL, or cached segments."""
    rows = [_BASE_ROW, _SMART_ROW, _EXTRA_ROW]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=FORMATS[k][0],
                    callback_data=f"{prefix}:{k}:{payload_id}",
                )
                for k in row
            ]
            for row in rows
        ]
    )


def _build_provider(settings: Settings):
    """Construct the configured default LLM provider, raising RuntimeError
    with a user-friendly message if no API key is set."""
    name = settings.default_provider
    api_key = settings.api_key_for(name)
    if not api_key:
        raise RuntimeError(
            f"Не задан API-ключ для провайдера {name!r}. Открой Настройки в окне приложения."
        )
    return make_provider(name, api_key=api_key)


async def deliver_result(
    bot: Bot,
    chat_id: int,
    segments: List[Segment],
    format_key: str,
    settings: Settings,
) -> None:
    """Produce the requested output from cached segments and ship it.

    Re-registers the same segments under a fresh short id afterwards and
    sends a new keyboard so the user can request another format without
    re-uploading the file.
    """
    label, kind, value = FORMATS[format_key]

    if kind == "tts":
        await _deliver_tts(bot, chat_id, segments, label, settings)
    else:
        if kind == "format":
            text = Formatter.format(segments, value)  # type: ignore[arg-type]
            ext = ".srt" if value == OutputFormat.SRT else ".txt"
        else:  # "summary"
            input_format = (
                OutputFormat.TIMESTAMPS
                if value in TIMESTAMPED_MODES
                else OutputFormat.TEXT
            )
            raw = Formatter.format(segments, input_format)
            if not raw.strip():
                await bot.send_message(
                    chat_id, "⚠️ Пустой транскрипт — нечего обрабатывать."
                )
                return
            try:
                provider = _build_provider(settings)
            except RuntimeError as exc:
                await bot.send_message(chat_id, f"❌ {exc}")
                return
            text = await Summarizer.process(provider, value, raw)  # type: ignore[arg-type]
            ext = ".txt"

        if not text:
            await bot.send_message(chat_id, "⚠️ Пустой результат.")
            return

        if kind == "summary" or len(text) > 4000:
            stem = (
                f"summary_{value.value}"  # type: ignore[union-attr]
                if kind == "summary"
                else "transcription"
            )
            file_bytes = text.encode("utf-8")
            await bot.send_document(
                chat_id,
                document=BufferedInputFile(file_bytes, f"{stem}{ext}"),
                caption=f"✅ Готово: {label}\nСимволов: {len(text)}",
            )
        else:
            await bot.send_message(chat_id, f"✅ Готово: {label}\n\n{text}")

    new_id = transcript_cache.register(segments)
    await bot.send_message(
        chat_id,
        "Хочешь ещё что-нибудь по этому же файлу? Жми любой формат:",
        reply_markup=build_keyboard("rpt", new_id),
    )


async def _deliver_tts(
    bot: Bot,
    chat_id: int,
    segments: List[Segment],
    label: str,
    settings: Settings,
) -> None:
    """Synthesize the BRIEF summary into audio and send it."""
    raw = Formatter.format(segments, OutputFormat.TEXT)
    if not raw.strip():
        await bot.send_message(chat_id, "⚠️ Пустой транскрипт — нечего озвучивать.")
        return

    await bot.send_message(chat_id, "🧠 Готовлю тезисы для озвучки...")
    try:
        provider = _build_provider(settings)
    except RuntimeError as exc:
        await bot.send_message(chat_id, f"❌ {exc}")
        return
    summary = await Summarizer.process(provider, SummaryMode.BRIEF, raw)
    if not summary.strip():
        await bot.send_message(chat_id, "⚠️ Не получил саммари для озвучки.")
        return

    await bot.send_message(chat_id, "🔊 Синтезирую речь (silero)...")
    out_path = config.temp_dir / f"tts_{uuid.uuid4().hex[:8]}.wav"
    try:
        await TTSService.synthesize(summary, out_path, speaker=settings.tts_speaker)
        await bot.send_audio(
            chat_id,
            audio=FSInputFile(out_path),
            title="Озвученные тезисы",
            caption=f"✅ Готово: {label} (озвучка тезисов)",
        )
    finally:
        Path(out_path).unlink(missing_ok=True)
