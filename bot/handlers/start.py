"""``/start``, ``/help``, ``/history``, ``/status`` plus the inline
"choose history item" callback flow.

Same UX as transcription-bot. The only meaningful change: ``history.add``
now stores ``user_id`` as ``str`` (per the desktop+bot unified history
table), so we cast Telegram's int ids to strings everywhere.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core import Settings, history, transcript_cache
from core.assets import bot_welcome_photo_path
from core.history import is_owner, owner_scope

from ._formats import build_keyboard


def _scope_for(settings: Settings, telegram_id: int) -> tuple[str, ...]:
    """Owner sees pooled history (desktop + own bot); everyone else sees
    only their own bot messages."""
    if is_owner(settings, telegram_id):
        return owner_scope(settings)
    return (str(telegram_id),)


def _label_with_source(item: dict) -> str:
    """Prefix the history label with a glyph showing where the row came
    from (📱 = sent in Telegram, 💻 = made on the desktop). Falls back to
    just the label if the field is missing (older rows)."""
    uid = item.get("user_id", "")
    glyph = "💻" if uid == "desktop" else "📱"
    return f"{glyph} {item['label']}"


_START_CAPTION = (
    "🎙 *Привет!* Я расшифровываю аудио и видео в текст.\n\n"
    "*Что присылать:*\n"
    "🎤 Голосовые · кружочки · аудио · видео\n"
    "🔗 Ссылки: YouTube · RuTube · VK · Я.Диск · Google Drive · прямые URL\n\n"
    "*Что получишь* (выберешь кнопкой после отправки):\n"
    "📝 Сплошной текст · ⏱ Таймкоды · 📺 SRT субтитры\n"
    "📋 Тезисы · 📚 По разделам · 🎭 По ролям · ❓ Вопросы · 🔊 Озвучка\n\n"
    "*Полезные команды:*\n"
    "/history — твои последние транскрипции (можно перезапросить любой формат без новой обработки)\n"
    "/help — подробнее\n\n"
    "Просто отправь файл или ссылку 👇"
)

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📚 Моя история", callback_data="cmd:history"),
                InlineKeyboardButton(text="❔ Подробнее", callback_data="cmd:help"),
            ]
        ]
    )
    # Photo + caption when the welcome image is bundled; plain text in
    # dev runs where assets/ may be missing.
    photo_path = bot_welcome_photo_path()
    if photo_path:
        await message.answer_photo(
            FSInputFile(photo_path),
            caption=_START_CAPTION,
            parse_mode="Markdown",
            reply_markup=kb,
        )
    else:
        await message.answer(
            _START_CAPTION,
            parse_mode="Markdown",
            reply_markup=kb,
        )


@router.callback_query(F.data == "cmd:history")
async def _cb_history(callback: CallbackQuery, bot: Bot, settings: Settings) -> None:
    await callback.answer()
    await _render_history(callback.message, callback.from_user.id, settings)


@router.callback_query(F.data == "cmd:help")
async def _cb_help(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    await cmd_help(callback.message)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "*Как использовать бот:*\n\n"
        "1️⃣ Отправь аудио, видео или ссылку\n"
        "2️⃣ Выбери формат вывода кнопками\n"
        "3️⃣ Дождись результата\n\n"
        "*Команды:*\n"
        "/start — начало работы\n"
        "/help — эта справка\n"
        "/history — последние 10 транскрипций\n"
        "/status — статус обработки\n\n"
        "*Лимиты:*\n"
        "• Максимальная длительность: 4 часа\n"
        "• Обработка длинных файлов может занять несколько часов\n\n"
        "⚠️ Длинные файлы обрабатываются на CPU, "
        "скорость примерно 0.3-0.5x от реального времени.",
        parse_mode="Markdown",
    )


def _format_created_at(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return iso_str


async def _render_history(message: Message, user_id: int, settings: Settings) -> None:
    scope = _scope_for(settings, user_id)
    items = history.recent(scope, limit=10)
    if not items:
        await message.answer(
            "📭 История пуста — ты ещё ничего не транскрибировал.\n\n"
            "Отправь голосовое, файл или ссылку, и оно появится здесь."
        )
        return

    rows = []
    for item in items:
        label = (
            f"📅 {_format_created_at(item['created_at'])} · "
            f"{_label_with_source(item)}"
        )
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"hist:{item['id']}")
        ])

    await message.answer(
        f"📚 Последние транскрипции ({len(items)}):\n_Жми, чтобы выбрать формат._",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="Markdown",
    )


@router.message(Command("history"))
async def cmd_history(message: Message, settings: Settings) -> None:
    await _render_history(message, message.from_user.id, settings)


@router.callback_query(F.data.startswith("hist:"))
async def handle_history_select(callback: CallbackQuery, bot: Bot, settings: Settings) -> None:
    await callback.answer()

    parts = callback.data.split(":")
    if len(parts) != 2:
        return
    try:
        transcript_id = int(parts[1])
    except ValueError:
        return

    scope = _scope_for(settings, callback.from_user.id)
    segments = history.get_segments(transcript_id, scope)
    if not segments:
        await callback.message.edit_text("❌ Транскрипция не найдена в истории.")
        return

    cache_id = transcript_cache.register(segments)
    await callback.message.edit_text(
        "📚 Выбери формат для этой транскрипции:",
        reply_markup=build_keyboard("rpt", cache_id),
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(
        "📊 *Статус:*\n\n"
        "Очередь: 0 задач\n"
        "Обработка: —\n\n"
        "_Функция в разработке_",
        parse_mode="Markdown",
    )
