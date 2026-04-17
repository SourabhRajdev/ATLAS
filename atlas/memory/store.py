"""SQLite-backed memory — conversations, facts, action log.

Features:
- FTS5 full-text search
- Semantic vector search (bge-small, local)
- World-state snapshots for temporal queries
- Signal feedback table for autonomy learning
- Importance scoring (0.0-1.0)
- Automatic deduplication
- Recency-weighted retrieval
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from atlas.core.models import ActionRecord, MemoryEntry, Message
from atlas.memory.feedback import FeedbackStore
from atlas.memory.semantic import SemanticStore
from atlas.memory.snapshots import SnapshotStore

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    tool_data   TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT,
    confidence  REAL NOT NULL DEFAULT 0.8,
    importance  REAL NOT NULL DEFAULT 0.5,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    expires_at  TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, type,
    content=memories,
    content_rowid=rowid,
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, type)
    VALUES (new.rowid, new.content, new.type);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, type)
    VALUES ('delete', old.rowid, old.content, old.type);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, type)
    VALUES ('delete', old.rowid, old.content, old.type);
    INSERT INTO memories_fts(rowid, content, type)
    VALUES (new.rowid, new.content, new.type);
END;

CREATE TABLE IF NOT EXISTS action_log (
    id          TEXT PRIMARY KEY,
    tool_name   TEXT NOT NULL,
    params      TEXT,
    result      TEXT,
    tier        INTEGER NOT NULL DEFAULT 1,
    approved    INTEGER NOT NULL DEFAULT 1,
    error       TEXT,
    cost_usd    REAL NOT NULL DEFAULT 0.0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_tracker (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Importance weights by memory type
IMPORTANCE_DEFAULTS = {
    "preference": 0.8,
    "decision": 0.7,
    "contact": 0.6,
    "fact": 0.5,
    "note": 0.4,
}

# Similarity threshold for deduplication
DEDUP_THRESHOLD = 0.75


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self._init_schema()
        self.semantic = SemanticStore(self.db)
        self.snapshots = SnapshotStore(self.db)
        self.feedback = FeedbackStore(self.db)

    def _init_schema(self) -> None:
        self.db.executescript(SCHEMA)
        # Add columns if upgrading from v1 schema
        for col, default in [("importance", "0.5"), ("access_count", "0")]:
            try:
                self.db.execute(f"ALTER TABLE memories ADD COLUMN {col} REAL NOT NULL DEFAULT {default}")
            except sqlite3.OperationalError:
                pass  # column already exists

    def close(self) -> None:
        self.db.close()

    # ---- conversations ----

    def add_message(self, msg: Message) -> None:
        self.db.execute(
            "INSERT INTO messages (id, session_id, role, content, tool_data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg.id, msg.session_id, msg.role, msg.content,
             json.dumps(msg.tool_data) if msg.tool_data else None,
             msg.created_at),
        )
        self.db.commit()

    def get_session_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT role, content, tool_data FROM messages "
            "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        result = []
        for r in reversed(rows):
            entry: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["tool_data"]:
                entry["tool_data"] = json.loads(r["tool_data"])
            result.append(entry)
        return result

    # ---- long-term memory ----

    def add_memory(self, entry: MemoryEntry) -> None:
        """Add a memory with deduplication and importance scoring."""
        # Auto-assign importance
        importance = IMPORTANCE_DEFAULTS.get(entry.type, 0.5) * entry.confidence

        # Dedup check: find similar existing memories
        existing = self._find_similar(entry.content, threshold=DEDUP_THRESHOLD)
        if existing:
            # Update the existing memory instead of creating a duplicate
            best = existing[0]
            new_confidence = min(1.0, best["confidence"] + 0.1)
            new_importance = min(1.0, best["importance"] + 0.1)
            now = datetime.now(timezone.utc).isoformat()
            self.db.execute(
                "UPDATE memories SET content = ?, confidence = ?, importance = ?, "
                "updated_at = ?, access_count = access_count + 1 "
                "WHERE id = ?",
                (entry.content, new_confidence, new_importance, now, best["id"]),
            )
            self.db.commit()
            return

        # Insert new memory
        self.db.execute(
            "INSERT INTO memories "
            "(id, type, content, source, confidence, importance, access_count, created_at, updated_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (entry.id, entry.type, entry.content, entry.source,
             entry.confidence, importance, entry.created_at, entry.updated_at, entry.expires_at),
        )
        self.db.commit()

    def _find_similar(self, content: str, threshold: float = 0.75) -> list[dict]:
        """Find memories similar to the given content using text similarity."""
        # First narrow with FTS, then compare with SequenceMatcher
        words = content.split()[:5]  # use first 5 words for FTS pre-filter
        query = " ".join(words)

        try:
            candidates = self.db.execute(
                'SELECT m.id, m.content, m.confidence, m.importance '
                'FROM memories_fts f JOIN memories m ON f.rowid = m.rowid '
                'WHERE memories_fts MATCH ? LIMIT 20',
                (f'"{query}"',),
            ).fetchall()
        except sqlite3.OperationalError:
            candidates = []

        # Also check recent memories (FTS might miss short content)
        recent = self.db.execute(
            "SELECT id, content, confidence, importance FROM memories "
            "ORDER BY updated_at DESC LIMIT 30",
        ).fetchall()

        seen = set()
        all_candidates = []
        for c in list(candidates) + list(recent):
            cid = c["id"]
            if cid not in seen:
                seen.add(cid)
                all_candidates.append(dict(c))

        # Compare similarity
        matches = []
        content_lower = content.lower()
        for c in all_candidates:
            ratio = SequenceMatcher(None, content_lower, c["content"].lower()).ratio()
            if ratio >= threshold:
                c["similarity"] = ratio
                matches.append(c)

        return sorted(matches, key=lambda x: x["similarity"], reverse=True)

    def search_memories(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories ranked by relevance * importance * recency."""
        if not query.strip():
            return []

        safe_query = query.replace('"', '""')
        try:
            rows = self.db.execute(
                'SELECT m.id, m.type, m.content, m.confidence, m.importance, '
                'm.access_count, m.created_at, rank '
                'FROM memories_fts f '
                'JOIN memories m ON f.rowid = m.rowid '
                'WHERE memories_fts MATCH ? '
                'ORDER BY rank * m.importance LIMIT ?',
                (f'"{safe_query}"', limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = self.db.execute(
                "SELECT id, type, content, confidence, importance, access_count, "
                "created_at, 0 as rank FROM memories WHERE content LIKE ? LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()

        # Bump access count for returned results
        for r in rows:
            self.db.execute(
                "UPDATE memories SET access_count = access_count + 1 WHERE id = ?",
                (r["id"],),
            )
        if rows:
            self.db.commit()

        return [dict(r) for r in rows]

    def get_recent_memories(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, type, content, confidence, importance, access_count, created_at "
            "FROM memories ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- action log ----

    def log_action(self, record: ActionRecord) -> None:
        self.db.execute(
            "INSERT INTO action_log (id, tool_name, params, result, tier, approved, error, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.id, record.tool_name, json.dumps(record.params),
             json.dumps(record.result) if record.result is not None else None,
             record.tier, record.approved, record.error, record.cost_usd,
             record.created_at),
        )
        self.db.commit()

    def get_recent_actions(self, limit: int = 15) -> list[dict]:
        rows = self.db.execute(
            "SELECT tool_name, tier, approved, error, created_at "
            "FROM action_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---- cost tracking ----

    def log_cost(self, session_id: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self.db.execute(
            "INSERT INTO cost_tracker (session_id, input_tokens, output_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?)",
            (session_id, input_tokens, output_tokens, cost_usd),
        )
        self.db.commit()

    def get_session_cost(self, session_id: str) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_tracker WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["total"]

    def get_total_cost(self) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_tracker"
        ).fetchone()
        return row["total"]
