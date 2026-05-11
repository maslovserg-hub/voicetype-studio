"""Core backend for VoiceType Studio.

UI-agnostic. Used identically by ``desktop/`` and ``bot/``.

The public surface is the set of names re-exported here. Submodules
``history`` and ``transcript_cache`` are exposed as modules (callers do
``core.history.add(...)``) because their API is several functions, not a
single class.

The ``llm`` subpackage is also exposed so callers can do
``from core.llm import make_provider, SummaryMode`` while still being able to
write ``from core import Summarizer`` for the dispatcher.
"""

from . import history, llm, settings as settings_io, transcript_cache
from .config import AppConfig, config
from .converter import AudioConverter
from .downloader import Downloader
from .formatter import Formatter, OutputFormat
from .llm import (
    AnthropicProvider,
    GeminiProvider,
    LLMProvider,
    OpenAIProvider,
    PerplexityProvider,
    SummaryMode,
    TIMESTAMPED_MODES,
    make_provider,
)
from .settings import Settings
from .summarizer import Summarizer
from .transcriber import Segment, Transcriber, Word
from .tts import TTSService

__all__ = [
    "AnthropicProvider",
    "AppConfig",
    "AudioConverter",
    "Downloader",
    "Formatter",
    "GeminiProvider",
    "LLMProvider",
    "OpenAIProvider",
    "OutputFormat",
    "PerplexityProvider",
    "Segment",
    "Settings",
    "Summarizer",
    "SummaryMode",
    "TIMESTAMPED_MODES",
    "TTSService",
    "Transcriber",
    "Word",
    "config",
    "history",
    "llm",
    "make_provider",
    "settings_io",
    "transcript_cache",
]
