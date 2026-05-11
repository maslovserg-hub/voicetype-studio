"""Smoke tests for ``core/`` after the bot→core rename.

These do not exercise GigaAM/silero (heavy, network-dependent) — they just
verify that the package imports cleanly and that the SQLite history layer
round-trips a Segment with word timings.
"""

from __future__ import annotations

import importlib

import pytest


def test_imports() -> None:
    """All public names re-exported from ``core`` resolve."""
    from core import (
        AppConfig,
        AudioConverter,
        Downloader,
        Formatter,
        OutputFormat,
        Segment,
        TTSService,
        Transcriber,
        Word,
        config,
        history,
        transcript_cache,
    )

    # Sanity: the singleton is the dataclass we expect.
    assert isinstance(config, AppConfig)
    # Modules are real modules, not None.
    assert history.__name__ == "core.history"
    assert transcript_cache.__name__ == "core.transcript_cache"
    # Public classes / enums survived the move.
    assert OutputFormat.TEXT.value == "text"
    for cls in (Transcriber, Downloader, AudioConverter, Formatter, TTSService):
        assert isinstance(cls, type)
    # Word / Segment dataclasses.
    w = Word(start=0.0, end=0.5, text="hi")
    s = Segment(start=0.0, end=0.5, text="hi", words=[w])
    assert s.words[0].text == "hi"


def test_no_residual_bot_imports() -> None:
    """Guard rail: nothing in core/ should still pull from the old ``bot`` package."""
    import core  # noqa: F401  — triggers full submodule import via __init__

    for name, module in list(__import__("sys").modules.items()):
        if not name.startswith("core"):
            continue
        src = getattr(module, "__file__", None)
        if not src:
            continue
        with open(src, "r", encoding="utf-8") as f:
            text = f.read()
        # Tolerate the docstring mention but block real imports.
        for forbidden in ("from bot.", "import bot."):
            assert forbidden not in text, f"{name} still references {forbidden!r}"


def test_history_roundtrip(tmp_path, monkeypatch) -> None:
    """Insert one transcription, read it back, fetch its segments."""
    from core import Segment, Word, config, history

    # Redirect persistent state into the test's tmp dir before any DB call.
    monkeypatch.setattr(config, "data_dir", tmp_path)

    segments = [
        Segment(
            start=0.0,
            end=1.5,
            text="Привет мир",
            words=[
                Word(start=0.0, end=0.6, text="Привет"),
                Word(start=0.7, end=1.5, text="мир"),
            ],
        ),
        Segment(start=1.6, end=2.4, text="это тест.", words=[]),
    ]

    row_id = history.add(
        user_id="desktop",
        source_label="📎 sample.mp4",
        source="sample.mp4",
        segments=segments,
    )
    assert row_id >= 1

    rows = history.recent("desktop", limit=5)
    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["label"] == "📎 sample.mp4"

    fetched = history.get_segments(row_id, "desktop")
    assert fetched is not None
    assert len(fetched) == 2
    assert fetched[0].text == "Привет мир"
    assert len(fetched[0].words) == 2
    assert fetched[0].words[0].text == "Привет"
    assert fetched[1].words == []


def test_history_user_id_isolation(tmp_path, monkeypatch) -> None:
    """A row owned by 'desktop' must not surface for a Telegram user_id."""
    from core import Segment, config, history

    monkeypatch.setattr(config, "data_dir", tmp_path)

    history.add(
        user_id="desktop",
        source_label="local",
        source="x",
        segments=[Segment(start=0, end=1, text="a")],
    )

    assert history.recent("12345", limit=10) == []
    assert history.get_segments(1, "12345") is None


def test_config_paths_are_lazy(tmp_path, monkeypatch) -> None:
    """``temp_dir`` / ``history_db`` / ``silero_dir`` derive from data_dir."""
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    assert config.temp_dir == tmp_path / "tmp"
    assert config.history_db == tmp_path / "history.db"
    assert config.silero_dir == tmp_path / "silero"
