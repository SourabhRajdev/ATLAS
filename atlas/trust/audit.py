"""Append-only audit log for all trust decisions.

Uses SQLite triggers that RAISE(ABORT) on UPDATE and DELETE,
making the log forensically sound — entries can never be modified
or removed after writing (except by dropping the table entirely,
which would be an obvious intrusion indicator).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("atlas.trust.audit")

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS trust_audit (
    id           TEXT PRIMARY KEY,
    ts           REAL NOT NULL,
    tool_name    TEXT NOT NULL,
    params_hash  TEXT NOT NULL,
    params_json  TEXT NOT NULL,
    taint_level  INTEGER NOT NULL,
    taint_source TEXT NOT NULL,
    consequence  INTEGER NOT NULL,
    allowed      INTEGER NOT NULL,
    block_reason TEXT,
    result_hash  TEXT,
    session_id   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_trust_ts ON trust_audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_trust_tool ON trust_audit(tool_name, ts DESC);

-- Append-only enforcement: no UPDATE or DELETE ever
CREATE TRIGGER IF NOT EXISTS trust_audit_block_update
BEFORE UPDATE ON trust_audit
BEGIN
    SELECT RAISE(ABORT, 'trust_audit is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trust_audit_block_delete
BEFORE DELETE ON trust_audit
BEGIN
    SELECT RAISE(ABORT, 'trust_audit is append-only: DELETE is forbidden');
END;
"""


@dataclass
class AuditEntry:
    tool_name: str
    params: dict
    taint_level: int
    taint_source: str
    consequence: int
    allowed: bool
    block_reason: str | None = None
    result: str | None = None
    session_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)

    @property
    def params_hash(self) -> str:
        raw = json.dumps(self.params, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def params_json_truncated(self) -> str:
        raw = json.dumps(self.params, default=str)
        return raw[:2000] if len(raw) > 2000 else raw

    @property
    def result_hash(self) -> str | None:
        if self.result is None:
            return None
        return hashlib.sha256(self.result.encode()).hexdigest()[:16]


class AuditLog:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("AuditLog initialized: %s", db_path)

    def log(self, entry: AuditEntry) -> None:
        """Write one audit entry.

        SYNC ONLY — callers from async context MUST use:
            await asyncio.to_thread(self._audit.log, entry)
        Calling this directly from a coroutine will block the event loop
        and may cause concurrent-write corruption on the WAL.
        """
        import asyncio as _asyncio
        try:
            _asyncio.get_running_loop()
            import logging as _log
            _log.getLogger("atlas.trust.audit").warning(
                "AuditLog.log() called from async context — "
                "use asyncio.to_thread(). Stack may indicate a bug."
            )
        except RuntimeError:
            pass  # no running loop — correct usage
        try:
            self._conn.execute(
                """INSERT INTO trust_audit
                   (id, ts, tool_name, params_hash, params_json,
                    taint_level, taint_source, consequence, allowed,
                    block_reason, result_hash, session_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id, entry.ts, entry.tool_name,
                    entry.params_hash, entry.params_json_truncated,
                    entry.taint_level, entry.taint_source,
                    entry.consequence, int(entry.allowed),
                    entry.block_reason, entry.result_hash,
                    entry.session_id,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            # Never raise — audit failure must not block execution flow.
            # Log loudly instead: an audit failure is itself a security event.
            logger.error("AUDIT WRITE FAILED (security event): %s | entry=%s/%s",
                         e, entry.tool_name, entry.id)

    def get_recent(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM trust_audit
               ORDER BY ts DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as n FROM trust_audit").fetchone()
        return row["n"]

    def get_blocked(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM trust_audit
               WHERE allowed = 0
               ORDER BY ts DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def verify_triggers_active(self) -> bool:
        """Return True if both append-only triggers exist in sqlite_master."""
        rows = self._conn.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'trigger'
               AND tbl_name = 'trust_audit'
               AND name IN (
                   'trust_audit_block_update',
                   'trust_audit_block_delete'
               )"""
        ).fetchall()
        return len(rows) == 2

    def close(self) -> None:
        self._conn.close()
