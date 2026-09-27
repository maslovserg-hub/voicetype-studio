"""Map a format-button click to a final output.

Pure async, no UI — accepts segments + format key + settings, returns the
text to display. Lives outside ``transcriptor_window``
so we can unit-test it without spinning up Tk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from core import (
    Formatter,
    OutputFormat,
    Segment,
    Summarizer,
    SummaryMode,
    TIMESTAMPED_MODES,
    make_provider,
)
from core.settings import Settings

logger = logging.getLogger(__name__)


# Eight buttons surfaced under each completed transcription.
TEXT_FORMATS = ("text", "timestamps", "srt")
LLM_FORMATS = ("brief", "structured", "roles", "questions", "translate")
ALL_FORMATS = TEXT_FORMATS + LLM_FORMATS

ResultKind = Literal["text"]


@dataclass
class FormatResult:
    """What ``deliver_format`` produced: ``content`` is the rendered string."""

    kind: ResultKind
    content: str


_FORMAT_TO_OUTPUT = {
    "text": OutputFormat.TEXT,
    "timestamps": OutputFormat.TIMESTAMPS,
    "srt": OutputFormat.SRT,
}


def _llm_provider(settings: Settings):
    name = settings.default_provider
    api_key = settings.api_key_for(name)
    if not api_key:
        raise RuntimeError(
            f"Не задан API-ключ для провайдера {name!r}. Откройте Настройки."
        )
    return make_provider(name, api_key=api_key)


async def deliver_format(
    segments: list[Segment],
    format_key: str,
    settings: Settings,
) -> FormatResult:
    """Produce the user-facing artefact for one of the eight buttons."""
    if not segments:
        raise ValueError("deliver_format called on empty segments")
    if format_key not in ALL_FORMATS:
        raise ValueError(f"Unknown format key: {format_key!r}")

    if format_key in TEXT_FORMATS:
        text = Formatter.format(segments, _FORMAT_TO_OUTPUT[format_key])
        return FormatResult(kind="text", content=text)

    mode = SummaryMode(format_key)
    if mode in TIMESTAMPED_MODES:
        transcript = Formatter.format(segments, OutputFormat.TIMESTAMPS)
    else:
        transcript = Formatter.format(segments, OutputFormat.TEXT)
    provider = _llm_provider(settings)
    text = await Summarizer.process(provider, mode, transcript)
    return FormatResult(kind="text", content=text)


# --- format button labels (used by MessageWidget) ------------------------

FORMAT_LABELS: dict[str, str] = {
    "text": "📝 Текст",
    "timestamps": "⏱ Таймкоды",
    "srt": "📺 SRT",
    "brief": "📋 Тезисы",
    "structured": "📚 Конспект",
    "roles": "🎭 По ролям",
    "questions": "❓ Вопросы",
    "translate": "🌐 Перевод",
}


def file_extension_for(format_key: str) -> str:
    """File extension to suggest in 'Save as…' dialog."""
    if format_key == "srt":
        return ".srt"
    return ".txt"
