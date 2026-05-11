"""Tests for ``desktop.history_window`` — pure label helper + structural.

The Tk widget code (CTkScrollableFrame, button rows) is not exercised
here — same convention as test_settings_window.py.
"""

from __future__ import annotations

import inspect

import pytest


# --- format_history_label (pure) ----------------------------------------


def test_format_history_label_iso_date() -> None:
    from desktop.history_window import format_history_label

    label = format_history_label({
        "id": 1,
        "label": "📎 audio.mp3",
        "source": "C:/x.mp3",
        "created_at": "2026-05-11T18:42:07",
    })
    assert "2026-05-11 18:42" in label
    assert "audio.mp3" in label


def test_format_history_label_missing_date() -> None:
    from desktop.history_window import format_history_label

    label = format_history_label({
        "id": 1,
        "label": "🔗 YouTube",
        "source": "https://youtu.be/x",
        "created_at": "",
    })
    # No date → just the label survives.
    assert "YouTube" in label
    assert label.strip() == "🔗 YouTube"


def test_format_history_label_malformed_date_falls_through() -> None:
    """Bad ``created_at`` mustn't lose the label."""
    from desktop.history_window import format_history_label

    label = format_history_label({
        "id": 1,
        "label": "📎 z.mp3",
        "source": "",
        "created_at": "not-a-date",
    })
    # Raw string survives + label survives.
    assert "not-a-date" in label
    assert "z.mp3" in label


def test_format_history_label_empty_label_placeholder() -> None:
    from desktop.history_window import format_history_label

    label = format_history_label({
        "id": 1,
        "label": "",
        "source": "",
        "created_at": "2026-05-11T10:00:00",
    })
    assert "(без названия)" in label


# --- structural ---------------------------------------------------------


def test_history_window_module_imports() -> None:
    from desktop import history_window

    assert history_window.HistoryWindow is not None
    assert callable(history_window.open_history_window)
    assert callable(history_window.format_history_label)


def test_history_window_constants() -> None:
    from desktop.history_window import HistoryWindow

    assert HistoryWindow.USER_ID == "desktop"
    assert HistoryWindow.LIMIT > 0


def test_history_window_has_refresh() -> None:
    from desktop.history_window import HistoryWindow

    assert callable(getattr(HistoryWindow, "refresh", None))


def test_open_history_window_signature() -> None:
    """The factory takes ``on_open`` keyword-only — main.py passes it that way."""
    from desktop.history_window import open_history_window

    sig = inspect.signature(open_history_window)
    assert "on_open" in sig.parameters
    assert sig.parameters["on_open"].kind == inspect.Parameter.KEYWORD_ONLY
