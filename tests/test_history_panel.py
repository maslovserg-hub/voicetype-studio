"""Tests for ``desktop.history_panel`` — pure row-text helpers + structural.

The panel itself needs a Tk root, so only the helpers and the class
surface are checked here.
"""

from __future__ import annotations

import inspect


# --- pure helpers -------------------------------------------------------


def test_transcript_row_text_iso_date() -> None:
    from desktop.history_panel import transcript_row_text

    text = transcript_row_text({
        "id": 1,
        "label": "📎 audio.mp3",
        "created_at": "2026-05-11T18:42:07",
        "user_id": "desktop",
    })
    assert text == ("💻 📎 audio.mp3", "2026-05-11 18:42")


def test_transcript_row_text_missing_date() -> None:
    from desktop.history_panel import transcript_row_text

    text = transcript_row_text({
        "label": "🔗 YouTube", "created_at": "", "user_id": "desktop",
    })
    assert text == ("💻 🔗 YouTube", "")


def test_transcript_row_text_phone_glyph_for_telegram_rows() -> None:
    """Rows from the bot (non-"desktop" user_id) are flagged with 📱 —
    owners need to tell their two views apart."""
    from desktop.history_panel import transcript_row_text

    text = transcript_row_text({
        "label": "🎤 voice.ogg",
        "created_at": "2026-05-11T19:00:00",
        "user_id": "123456789",
    })
    assert text[0].startswith("📱 ")


def test_transcript_row_text_malformed_date_falls_through() -> None:
    from desktop.history_panel import transcript_row_text

    text = transcript_row_text({"label": "📎 z.mp3", "created_at": "not-a-date"})
    assert text[1] == "not-a-date"
    assert "z.mp3" in text[0]


def test_transcript_row_text_empty_label_placeholder() -> None:
    from desktop.history_panel import transcript_row_text

    assert "(без названия)" in transcript_row_text({"label": "", "created_at": ""})[0]


def test_shorten_keeps_short_and_cuts_long() -> None:
    from desktop.history_panel import shorten

    assert shorten("short.mp4", 20) == "short.mp4"
    cut = shorten("a" * 50, 20)
    assert len(cut) == 20 and cut.endswith("…")


# --- structural ---------------------------------------------------------


def test_owner_scope_pools_desktop_with_first_whitelist_id() -> None:
    """The desktop sees both its own rows and the owner's bot rows. The
    owner is the first entry in ``whitelist_ids`` — extra ids stay
    isolated so multi-user installs keep privacy."""
    from core import Settings
    from core.history import owner_scope

    assert owner_scope(Settings(whitelist_ids=[])) == ("desktop",)
    assert owner_scope(Settings(whitelist_ids=[123])) == ("desktop", "123")
    assert owner_scope(Settings(whitelist_ids=[123, 456, 789])) == ("desktop", "123")


def test_history_panel_surface() -> None:
    from desktop.history_panel import HistoryPanel

    assert HistoryPanel.LIMIT > 0
    assert callable(getattr(HistoryPanel, "refresh", None))
    params = inspect.signature(HistoryPanel.__init__).parameters
    for name in ("on_open", "get_settings"):
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY


def test_transcriptor_has_history_toggle_and_settings_hook() -> None:
    from desktop.transcriptor_window import TranscriptorWindow

    assert callable(getattr(TranscriptorWindow, "toggle_history", None))
    params = inspect.signature(TranscriptorWindow.__init__).parameters
    assert params["on_open_settings"].default is None
