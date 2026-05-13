"""Map a format-button click to a final output.

Pure async, no UI — accepts segments + format key + settings, returns the
text (or audio file path) to display. Lives outside ``transcriptor_window``
so we can unit-test it without spinning up Tk.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core import (
    Formatter,
    OutputFormat,
    Segment,
    Summarizer,
    SummaryMode,
    TIMESTAMPED_MODES,
    TTSService,
    config,
    make_provider,
)
from core.settings import Settings

logger = logging.getLogger(__name__)


# Eight buttons surfaced under each completed transcription.
TEXT_FORMATS = ("text", "timestamps", "srt")
LLM_FORMATS = ("brief", "structured", "roles", "questions")
TTS_FORMAT = "tts"
ALL_FORMATS = TEXT_FORMATS + LLM_FORMATS + (TTS_FORMAT,)

ResultKind = Literal["text", "audio_path"]


@dataclass
class FormatResult:
    """What ``deliver_format`` produced.

    For ``text`` results, ``content`` holds the rendered string.
    For ``audio_path`` (🔊 button), ``content`` is the absolute path to the
    generated WAV file and ``preview_text`` carries the brief that was
    synthesized — handy to show under the player.
    """

    kind: ResultKind
    content: str
    preview_text: str | None = None


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

    if format_key in LLM_FORMATS:
        mode = SummaryMode(format_key)
        if mode in TIMESTAMPED_MODES:
            transcript = Formatter.format(segments, OutputFormat.TIMESTAMPS)
        else:
            transcript = Formatter.format(segments, OutputFormat.TEXT)
        provider = _llm_provider(settings)
        text = await Summarizer.process(provider, mode, transcript)
        return FormatResult(kind="text", content=text)

    # TTS — synthesize the BRIEF (so its cache is reused by the textual
    # button as well) and write a wav next to the data dir.
    provider = _llm_provider(settings)
    transcript = Formatter.format(segments, OutputFormat.TEXT)
    brief = await Summarizer.process(provider, SummaryMode.BRIEF, transcript)
    out_dir = config.data_dir / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"tts_{uuid.uuid4().hex[:10]}.wav"
    await TTSService.synthesize(brief, out_path, speaker=settings.tts_speaker)
    logger.info("TTS rendered %s (%d chars)", out_path, len(brief))
    return FormatResult(
        kind="audio_path",
        content=str(out_path),
        preview_text=brief,
    )


# --- format button labels (used by MessageWidget) ------------------------

FORMAT_LABELS: dict[str, str] = {
    "text": "📝 Текст",
    "timestamps": "⏱ Таймкоды",
    "srt": "📺 SRT",
    "brief": "📋 Тезисы",
    "structured": "📚 Конспект",
    "roles": "🎭 По ролям",
    "questions": "❓ Вопросы",
    "tts": "🔊 Озвучка",
}


def file_extension_for(format_key: str) -> str:
    """File extension to suggest in 'Save as…' dialog."""
    if format_key == "srt":
        return ".srt"
    if format_key == "tts":
        return ".wav"
    return ".txt"
