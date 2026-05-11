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
from typing import List, Optional

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


def recent(user_id: str, limit: int = 10) -> List[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT id, source_label, source, created_at
               FROM transcriptions
               WHERE user_id = ?
               ORDER BY id DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    return [
        {"id": r[0], "label": r[1], "source": r[2], "created_at": r[3]} for r in rows
    ]


def get_segments(transcript_id: int, user_id: str) -> Optional[List[Segment]]:
    """Return segments for the given history row, only if it belongs to user_id."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT segments_json FROM transcriptions WHERE id = ? AND user_id = ?",
            (transcript_id, user_id),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
        return [_seg_from_dict(d) for d in data]
    except Exception:
        logger.exception("Failed to deserialize history row %d", transcript_id)
        return None


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
