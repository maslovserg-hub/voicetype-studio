"""End-to-end check of the desktop+bot history pool.

The owner (whitelist[0]) views a unified list of their desktop rows and
their bot rows; other Telegram users only see their own bot rows. These
tests drive ``core.history`` against a temp SQLite to make sure the
SQL ``user_id IN (...)`` plumbing actually does that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import Segment, Settings, history
from core.history import is_owner, owner_scope


@pytest.fixture
def fresh_history(tmp_path, monkeypatch):
    """Redirect history.db to a per-test temp file."""
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    yield tmp_path


def _seg(text: str) -> Segment:
    return Segment(start=0.0, end=1.0, text=text, words=[])


def test_recent_pools_desktop_and_owner(fresh_history) -> None:
    history.add("desktop", "💻 audio.mp3", "C:/x.mp3", [_seg("a")])
    history.add("999", "📱 other.mp3", "tg", [_seg("b")])
    history.add("123", "📱 voice.ogg", "tg", [_seg("c")])

    pooled = history.recent(("desktop", "123"), limit=10)
    # Owner sees their desktop row + their tg row, NOT the stranger's row.
    labels = [r["label"] for r in pooled]
    assert "💻 audio.mp3" in labels
    assert "📱 voice.ogg" in labels
    assert "📱 other.mp3" not in labels


def test_recent_string_arg_is_backwards_compatible(fresh_history) -> None:
    """Existing callers passing a single ``user_id`` string keep working
    after the list-scope refactor."""
    history.add("desktop", "💻 a.mp3", "C:/a.mp3", [_seg("a")])
    history.add("desktop", "💻 b.mp3", "C:/b.mp3", [_seg("b")])

    rows = history.recent("desktop")
    assert len(rows) == 2
    assert all(r["user_id"] == "desktop" for r in rows)


def test_get_segments_rejects_rows_outside_scope(fresh_history) -> None:
    """A foreign Telegram user must not be able to fetch the owner's
    desktop transcript by guessing its id."""
    row_id = history.add("desktop", "💻 secret.mp3", "C:/s.mp3", [_seg("hi")])

    # Owner scope works.
    assert history.get_segments(row_id, ("desktop", "123")) is not None
    # Stranger scope returns None even if the id is valid.
    assert history.get_segments(row_id, ("999",)) is None


def test_owner_scope_handles_iterables_other_than_lists() -> None:
    """``owner_scope`` builds the tuple from ``whitelist_ids``; the
    iterable type shouldn't matter and duplicates should drop."""
    s = Settings(whitelist_ids=[7, 7, 7])
    assert owner_scope(s) == ("desktop", "7")


def test_is_owner_checks_first_whitelist_id_only() -> None:
    s = Settings(whitelist_ids=[111, 222])
    assert is_owner(s, 111) is True
    # Second whitelist entry is NOT the owner — they get private history.
    assert is_owner(s, 222) is False
    assert is_owner(s, 999) is False

    empty = Settings(whitelist_ids=[])
    assert is_owner(empty, 111) is False
