"""Tests for ``desktop._format_dispatch.deliver_format``.

The three deterministic formats (text/timestamps/srt) hit ``core.Formatter``
directly. The four LLM modes go through ``Summarizer.process`` — we wire a
fake provider via :func:`monkeypatch.setattr` against the LLM registry so
no network is touched. TTS is exercised by stubbing ``TTSService.synthesize``.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from core import Segment, Settings, Summarizer, SummaryMode, Word
from core.llm.base import KNOWN_PROVIDERS, LLMProvider
from desktop._format_dispatch import (
    ALL_FORMATS,
    FORMAT_LABELS,
    LLM_FORMATS,
    TEXT_FORMATS,
    TTS_FORMAT,
    deliver_format,
    file_extension_for,
)


# --- fixtures ------------------------------------------------------------


def _segments() -> list[Segment]:
    return [
        Segment(
            start=0.0, end=2.0, text="Привет мир, это первое предложение.",
            words=[
                Word(start=0.0, end=0.4, text="Привет"),
                Word(start=0.4, end=0.7, text="мир,"),
                Word(start=0.7, end=1.0, text="это"),
                Word(start=1.0, end=1.4, text="первое"),
                Word(start=1.4, end=2.0, text="предложение."),
            ],
        ),
        Segment(start=2.0, end=3.5, text="Второе предложение тут.", words=[]),
    ]


class _FakeProvider(LLMProvider):
    name: ClassVar[str] = "fake"
    default_model: ClassVar[str] = "fake-1"
    available_models: ClassVar[tuple[str, ...]] = ("fake-1",)

    def __init__(self, api_key: str = "k", model: str | None = None):
        super().__init__(api_key, model)
        self.calls: list[tuple[SummaryMode, str]] = []

    async def process(self, mode, transcript, model=None):
        self.calls.append((mode, transcript))
        return f"[{mode.value}] {transcript[:40]}…"


@pytest.fixture(autouse=True)
def _wire_fake_provider():
    """Register 'fake' under KNOWN_PROVIDERS for the duration of each test."""
    KNOWN_PROVIDERS["fake"] = _FakeProvider
    Summarizer.clear_cache()
    yield
    KNOWN_PROVIDERS.pop("fake", None)
    Summarizer.clear_cache()


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(
        default_provider="fake",
        api_keys={"fake": "test-key"},
    )


# --- structural ----------------------------------------------------------


def test_all_formats_have_labels() -> None:
    for key in ALL_FORMATS:
        assert key in FORMAT_LABELS
        assert FORMAT_LABELS[key]


def test_format_groups_disjoint() -> None:
    text = set(TEXT_FORMATS)
    llm = set(LLM_FORMATS)
    assert not (text & llm)
    assert TTS_FORMAT not in text and TTS_FORMAT not in llm


def test_file_extension_mapping() -> None:
    assert file_extension_for("srt") == ".srt"
    assert file_extension_for("tts") == ".wav"
    assert file_extension_for("text") == ".txt"
    assert file_extension_for("brief") == ".txt"


# --- deterministic formats ----------------------------------------------


def test_deliver_text(fake_settings) -> None:
    result = asyncio.run(deliver_format(_segments(), "text", fake_settings))
    assert result.kind == "text"
    assert "Привет мир" in result.content
    assert "Второе" in result.content


def test_deliver_timestamps(fake_settings) -> None:
    result = asyncio.run(deliver_format(_segments(), "timestamps", fake_settings))
    assert result.kind == "text"
    assert "[00:00]" in result.content


def test_deliver_srt(fake_settings) -> None:
    result = asyncio.run(deliver_format(_segments(), "srt", fake_settings))
    assert result.kind == "text"
    assert "-->" in result.content


# --- LLM formats ---------------------------------------------------------


def test_deliver_brief_uses_plain_transcript(fake_settings) -> None:
    result = asyncio.run(deliver_format(_segments(), "brief", fake_settings))
    assert result.kind == "text"
    assert result.content.startswith("[brief]")


def test_deliver_questions_uses_timestamped_transcript(fake_settings) -> None:
    """``questions`` is the only LLM mode that wants timestamps in the input."""
    result = asyncio.run(deliver_format(_segments(), "questions", fake_settings))
    assert result.kind == "text"
    # Last fake provider call should have received a timestamped transcript.
    # Find the FakeProvider instance via Summarizer's recent calls — easier:
    # reach into KNOWN_PROVIDERS, instantiate again to inspect would lose state.
    # Trick: construct one explicitly and re-run with a small transcript.


def test_questions_payload_is_timestamped(fake_settings) -> None:
    """End-to-end: when 'questions' is asked, the transcript handed to the
    provider includes [HH:MM:SS] markers."""
    captured: list[str] = []

    class _Capturing(_FakeProvider):
        async def process(self, mode, transcript, model=None):
            captured.append(transcript)
            return await super().process(mode, transcript, model)

    KNOWN_PROVIDERS["fake"] = _Capturing
    asyncio.run(deliver_format(_segments(), "questions", fake_settings))
    assert captured, "fake provider was not called"
    assert "[" in captured[0] and "]" in captured[0]


def test_brief_payload_is_plain(fake_settings) -> None:
    captured: list[str] = []

    class _Capturing(_FakeProvider):
        async def process(self, mode, transcript, model=None):
            captured.append(transcript)
            return await super().process(mode, transcript, model)

    KNOWN_PROVIDERS["fake"] = _Capturing
    asyncio.run(deliver_format(_segments(), "brief", fake_settings))
    assert captured
    # Plain transcript shouldn't have bracketed timestamps at line starts.
    assert not captured[0].lstrip().startswith("[")


def test_missing_api_key_raises(fake_settings) -> None:
    bad = Settings(default_provider="fake", api_keys={})  # no key
    with pytest.raises(RuntimeError, match="API-ключ"):
        asyncio.run(deliver_format(_segments(), "brief", bad))


def test_unknown_format_raises(fake_settings) -> None:
    with pytest.raises(ValueError, match="Unknown format"):
        asyncio.run(deliver_format(_segments(), "bogus", fake_settings))


def test_empty_segments_raises(fake_settings) -> None:
    with pytest.raises(ValueError, match="empty segments"):
        asyncio.run(deliver_format([], "text", fake_settings))


# --- TTS branch ----------------------------------------------------------


def test_deliver_tts_writes_wav(fake_settings, tmp_path, monkeypatch) -> None:
    # Stub TTSService.synthesize so we don't pull torch/silero in tests.
    from core import TTSService, config

    monkeypatch.setattr(config, "data_dir", tmp_path)

    async def fake_synthesize(text: str, output_path, speaker=None):
        from pathlib import Path
        Path(output_path).write_bytes(b"RIFFfaked WAV bytes")
        return output_path

    monkeypatch.setattr(TTSService, "synthesize", staticmethod(fake_synthesize))

    result = asyncio.run(deliver_format(_segments(), "tts", fake_settings))
    assert result.kind == "audio_path"
    assert result.preview_text and result.preview_text.startswith("[brief]")
    from pathlib import Path
    assert Path(result.content).exists()
    assert Path(result.content).suffix == ".wav"


def test_deliver_tts_passes_speaker_from_settings(
    fake_settings, tmp_path, monkeypatch,
) -> None:
    """Regression — earlier ``TTSService._resolve_speaker`` ignored
    ``Settings.tts_speaker`` and read ``TTS_SPEAKER`` env var instead, so
    Settings UI couldn't actually switch voices. The dispatcher must pass
    the configured speaker through."""
    from core import TTSService, Settings, config

    monkeypatch.setattr(config, "data_dir", tmp_path)

    captured: dict = {}

    async def capturing_synth(text: str, output_path, speaker=None):
        from pathlib import Path
        captured["speaker"] = speaker
        Path(output_path).write_bytes(b"x")
        return output_path

    monkeypatch.setattr(TTSService, "synthesize", staticmethod(capturing_synth))

    s = Settings(
        default_provider="fake",
        api_keys={"fake": "k"},
        tts_speaker="aidar",
    )
    asyncio.run(deliver_format(_segments(), "tts", s))
    assert captured["speaker"] == "aidar"
