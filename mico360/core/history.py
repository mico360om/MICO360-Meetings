"""Local project history (SQLite). Stores transcripts and generated minutes
so meetings can be searched, reopened, edited and re-exported — all offline.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import DB_FILE

log = logging.getLogger("mico360.history")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL DEFAULT 'Untitled meeting',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    source_type TEXT DEFAULT '',
    source_path TEXT DEFAULT '',
    style       TEXT DEFAULT '',
    model       TEXT DEFAULT '',
    profile_id  TEXT DEFAULT '',
    transcript  TEXT DEFAULT '',
    minutes     TEXT DEFAULT ''
);
"""


@dataclass
class Meeting:
    id: int
    title: str
    created_at: float
    updated_at: float
    source_type: str = ""
    source_path: str = ""
    style: str = ""
    model: str = ""
    profile_id: str = ""
    transcript: str = ""
    minutes: str = ""


class History:
    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def _row(self, r: sqlite3.Row) -> Meeting:
        return Meeting(**{k: r[k] for k in r.keys()})

    def save(self, m: Meeting) -> int:
        now = time.time()
        if m.id and self.get(m.id):
            self._conn.execute(
                """UPDATE meetings SET title=?, updated_at=?, source_type=?, source_path=?,
                   style=?, model=?, profile_id=?, transcript=?, minutes=? WHERE id=?""",
                (m.title, now, m.source_type, m.source_path, m.style, m.model,
                 m.profile_id, m.transcript, m.minutes, m.id),
            )
            self._conn.commit()
            return m.id
        cur = self._conn.execute(
            """INSERT INTO meetings (title, created_at, updated_at, source_type, source_path,
               style, model, profile_id, transcript, minutes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (m.title, now, now, m.source_type, m.source_path, m.style, m.model,
             m.profile_id, m.transcript, m.minutes),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get(self, meeting_id: int) -> Meeting | None:
        r = self._conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        return self._row(r) if r else None

    def delete(self, meeting_id: int) -> None:
        self._conn.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
        self._conn.commit()

    def list(self, search: str = "", limit: int = 500) -> list[Meeting]:
        if search.strip():
            like = f"%{search.strip()}%"
            rows = self._conn.execute(
                """SELECT * FROM meetings
                   WHERE title LIKE ? OR transcript LIKE ? OR minutes LIKE ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (like, like, like, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM meetings ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(r) for r in rows]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
