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
        "user_id": "desktop",
    })
    # No date → just the label survives, with a source glyph (💻 here).
    assert "YouTube" in label
    assert label.strip() == "💻 🔗 YouTube"


def test_format_history_label_uses_phone_glyph_for_telegram_rows() -> None:
    """Rows that originated in the bot (non-"desktop" user_id) should be
    flagged with 📱 — owners need to tell their two views apart."""
    from desktop.history_window import format_history_label

    label = format_history_label({
        "id": 7,
        "label": "🎤 voice.ogg",
        "source": "tg",
        "created_at": "2026-05-11T19:00:00",
        "user_id": "123456789",
    })
    assert "📱" in label
    assert "voice.ogg" in label


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

    assert HistoryWindow.LIMIT > 0


def test_owner_scope_pools_desktop_with_first_whitelist_id() -> None:
    """The desktop sees both its own rows and the owner's bot rows. The
    owner is the first entry in ``whitelist_ids`` — extra ids stay
    isolated so multi-user installs keep privacy."""
    from core import Settings
    from core.history import owner_scope

    empty = Settings(whitelist_ids=[])
    assert owner_scope(empty) == ("desktop",)

    one = Settings(whitelist_ids=[123])
    assert owner_scope(one) == ("desktop", "123")

    many = Settings(whitelist_ids=[123, 456, 789])
    # Only whitelist[0] is the owner; the others stay private.
    assert owner_scope(many) == ("desktop", "123")


def test_history_window_has_refresh() -> None:
    from desktop.history_window import HistoryWindow

    assert callable(getattr(HistoryWindow, "refresh", None))


def test_open_history_window_signature() -> None:
    """The factory takes ``on_open`` keyword-only — main.py passes it that way."""
    from desktop.history_window import open_history_window

    sig = inspect.signature(open_history_window)
    assert "on_open" in sig.parameters
    assert sig.parameters["on_open"].kind == inspect.Parameter.KEYWORD_ONLY
