"""World state snapshot storage — periodic snapshots for temporal queries.

"What was I doing at 3pm yesterday?" is answered by scanning the snapshot table,
not by replaying logs. Snapshots are compact (< 500 bytes each) and kept for 30
days.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


class SnapshotStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self._init_table()

    def _init_table(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS world_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL NOT NULL,
                app         TEXT,
                window      TEXT,
                document    TEXT,
                url         TEXT,
                is_idle     INTEGER NOT NULL DEFAULT 0,
                summary     TEXT,
                metadata    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ws_ts ON world_snapshots(ts);
        """)

    def save(self, world_state) -> None:
        self.db.execute(
            "INSERT INTO world_snapshots (ts, app, window, document, url, is_idle, summary, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                world_state.timestamp,
                world_state.active_app or "",
                world_state.active_window_title or "",
                str(world_state.active_document) if world_state.active_document else "",
                world_state.active_url or "",
                1 if world_state.is_idle() else 0,
                world_state.to_summary(),
                json.dumps({"recent_apps": [a for a, _ in world_state.recent_apps[:5]]}),
            ),
        )
        self.db.commit()

    def query_range(self, start_ts: float, end_ts: float, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT ts, app, window, document, url, is_idle, summary "
            "FROM world_snapshots WHERE ts BETWEEN ? AND ? ORDER BY ts LIMIT ?",
            (start_ts, end_ts, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def latest(self, n: int = 1) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT ts, app, window, document, url, is_idle, summary "
            "FROM world_snapshots ORDER BY ts DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, days: int = 30) -> int:
        cutoff = time.time() - days * 86400
        cur = self.db.execute("DELETE FROM world_snapshots WHERE ts < ?", (cutoff,))
        self.db.commit()
        return cur.rowcount

    def active_at(self, ts: float) -> dict[str, Any] | None:
        """Nearest snapshot to a timestamp (within 5 min)."""
        row = self.db.execute(
            "SELECT ts, app, window, document, url, is_idle, summary "
            "FROM world_snapshots WHERE ts BETWEEN ? AND ? "
            "ORDER BY ABS(ts - ?) LIMIT 1",
            (ts - 300, ts + 300, ts),
        ).fetchone()
        return dict(row) if row else None
