"""Persistent transcription history backed by SQLite.

Stores enough to rebuild the segment list (with words) on demand, so the user
can ask for any format/summary later without re-transcribing.

``user_id`` is stored as TEXT — it holds either the Telegram numeric id (as a
string) or the literal ``"desktop"`` for transcriptor-window jobs. Dictation
results are not saved (по плану — это «голосовая клавиатура»).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Iterable, List, Optional, Union

UserScope = Union[str, Iterable[str]]

from .config import config
from .transcriber import Segment, Word

logger = logging.getLogger(__name__)


def _db_path():
    return config.history_db


def _connect() -> sqlite3.Connection:
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transcriptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            source_label    TEXT NOT NULL,
            source          TEXT,
            created_at      TEXT NOT NULL,
            segments_json   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transcriptions_user "
        "ON transcriptions(user_id, id DESC)"
    )
    return conn


def add(user_id: str, source_label: str, source: str, segments: List[Segment]) -> int:
    """Save a transcription. Returns the new row id."""
    payload = json.dumps([_seg_to_dict(s) for s in segments], ensure_ascii=False)
    with closing(_connect()) as conn:
        cur = conn.execute(
            """INSERT INTO transcriptions
               (user_id, source_label, source, created_at, segments_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                user_id,
                source_label,
                source,
                datetime.now().isoformat(timespec="seconds"),
                payload,
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        logger.info("history.add user=%s id=%d label=%s", user_id, new_id, source_label)
        return new_id


def _normalize_scope(scope: UserScope) -> tuple[str, ...]:
    """Accept either a single ``user_id`` (back-compat) or an iterable of them
    (owner-mode where desktop + a Telegram id share a view) and return a
    deduplicated tuple safe for parameter substitution."""
    if isinstance(scope, str):
        return (scope,) if scope else ()
    seen: list[str] = []
    for u in scope:
        if u and u not in seen:
            seen.append(u)
    return tuple(seen)


def recent(scope: UserScope, limit: int = 10) -> List[dict]:
    """Return up to ``limit`` most recent history rows visible to ``scope``.

    ``scope`` may be a single ``user_id`` or an iterable of ids — pass
    ``["desktop", str(telegram_id)]`` for an owner view that pools desktop
    and personal bot history together.
    """
    ids = _normalize_scope(scope)
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"""SELECT id, user_id, source_label, source, created_at
                FROM transcriptions
                WHERE user_id IN ({placeholders})
                ORDER BY id DESC
                LIMIT ?""",
            (*ids, limit),
        ).fetchall()
    return [
        {
            "id": r[0],
            "user_id": r[1],
            "label": r[2],
            "source": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]


def get_segments(transcript_id: int, scope: UserScope) -> Optional[List[Segment]]:
    """Return segments for the given history row, only if it belongs to one
    of the user-ids in ``scope`` (or matches ``scope`` directly when a single
    string is passed)."""
    ids = _normalize_scope(scope)
    if not ids:
        return None
    placeholders = ",".join("?" * len(ids))
    with closing(_connect()) as conn:
        row = conn.execute(
            f"SELECT segments_json FROM transcriptions "
            f"WHERE id = ? AND user_id IN ({placeholders})",
            (transcript_id, *ids),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
        return [_seg_from_dict(d) for d in data]
    except Exception:
        logger.exception("Failed to deserialize history row %d", transcript_id)
        return None


def owner_scope(settings) -> tuple[str, ...]:
    """Build the "owner view" history scope from a :class:`core.Settings`.

    ``whitelist_ids[0]`` is treated as the desktop user's Telegram id —
    pooling their bot history with the local ``"desktop"`` rows. Other
    whitelist members stay isolated and only see their own messages.
    Returns at least ``("desktop",)`` so the desktop UI always has its
    own rows even when no Telegram is configured.
    """
    ids: list[str] = ["desktop"]
    wl = getattr(settings, "whitelist_ids", None) or []
    if wl:
        ids.append(str(wl[0]))
    return tuple(ids)


def is_owner(settings, telegram_id: int) -> bool:
    """True if ``telegram_id`` is the configured owner (first whitelist id)."""
    wl = getattr(settings, "whitelist_ids", None) or []
    return bool(wl) and int(wl[0]) == int(telegram_id)


def _seg_to_dict(s: Segment) -> dict:
    return {
        "start": s.start,
        "end": s.end,
        "text": s.text,
        "words": [{"start": w.start, "end": w.end, "text": w.text} for w in s.words],
    }


def _seg_from_dict(d: dict) -> Segment:
    return Segment(
        start=d["start"],
        end=d["end"],
        text=d["text"],
        words=[Word(**w) for w in d.get("words", [])],
    )
