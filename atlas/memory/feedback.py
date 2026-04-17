"""Signal feedback table — tracks user acceptance/dismissal of suggestions.

Used by the autonomy learner (EMA weights) and also available for direct
queries ("show me what I dismissed last week").
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any


class FeedbackStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self._init_table()

    def _init_table(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS signal_feedback (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_source   TEXT NOT NULL,
                signal_kind     TEXT NOT NULL,
                suggestion_id   TEXT,
                accepted        INTEGER NOT NULL,   -- 1 accepted, 0 dismissed
                confidence      REAL,
                ts              REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sf_source ON signal_feedback(signal_source, signal_kind);
        """)

    def record(self, source: str, kind: str, accepted: bool,
               suggestion_id: str = "", confidence: float = 0.0) -> None:
        self.db.execute(
            "INSERT INTO signal_feedback (signal_source, signal_kind, suggestion_id, accepted, confidence, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source, kind, suggestion_id, 1 if accepted else 0, confidence, time.time()),
        )
        self.db.commit()

    def acceptance_rate(self, source: str, kind: str, days: int = 30) -> float | None:
        cutoff = time.time() - days * 86400
        rows = self.db.execute(
            "SELECT accepted FROM signal_feedback WHERE signal_source = ? AND signal_kind = ? AND ts > ?",
            (source, kind, cutoff),
        ).fetchall()
        if not rows:
            return None
        return sum(r["accepted"] for r in rows) / len(rows)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT signal_source, signal_kind, suggestion_id, accepted, confidence, ts "
            "FROM signal_feedback ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
