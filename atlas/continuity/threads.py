"""Thread management — Maintains ongoing contexts across time."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atlas.continuity.models import Thread, ThreadState, ThreadType, ThreadUpdate

logger = logging.getLogger("atlas.continuity")

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id       TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    state           TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    context         TEXT,
    created_at      TEXT NOT NULL,
    last_update     TEXT NOT NULL,
    last_surfaced   TEXT,
    priority        REAL NOT NULL DEFAULT 0.5,
    session_id      TEXT,
    related_memories TEXT,
    tags            TEXT
);

CREATE INDEX IF NOT EXISTS idx_state ON threads(state);
CREATE INDEX IF NOT EXISTS idx_priority ON threads(priority DESC);
CREATE INDEX IF NOT EXISTS idx_last_update ON threads(last_update DESC);

CREATE TABLE IF NOT EXISTS thread_updates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       TEXT NOT NULL,
    update_type     TEXT NOT NULL,
    content         TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    metadata        TEXT,
    FOREIGN KEY (thread_id) REFERENCES threads(thread_id)
);

CREATE INDEX IF NOT EXISTS idx_thread_updates ON thread_updates(thread_id, timestamp DESC);
"""


class ThreadManager:
    """Manages ongoing context threads."""
    
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
    
    def close(self) -> None:
        """Close database connection."""
        self.db.close()
    
    def create_thread(
        self,
        type: ThreadType,
        title: str,
        description: str = "",
        context: dict[str, Any] | None = None,
        priority: float = 0.5,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> Thread:
        """Create a new thread."""
        now = datetime.now(timezone.utc).isoformat()
        
        thread = Thread(
            thread_id=uuid.uuid4().hex[:12],
            type=type,
            state=ThreadState.ACTIVE,
            title=title,
            description=description,
            context=context or {},
            created_at=now,
            last_update=now,
            priority=priority,
            session_id=session_id,
            tags=tags or [],
        )
        
        self.db.execute(
            "INSERT INTO threads "
            "(thread_id, type, state, title, description, context, created_at, "
            "last_update, priority, session_id, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread.thread_id,
                thread.type.value,
                thread.state.value,
                thread.title,
                thread.description,
                json.dumps(thread.context),
                thread.created_at,
                thread.last_update,
                thread.priority,
                thread.session_id,
                json.dumps(thread.tags),
            ),
        )
        self.db.commit()
        
        logger.info("Created thread: %s (%s)", thread.title, thread.thread_id)
        return thread
    
    def get_thread(self, thread_id: str) -> Thread | None:
        """Get a thread by ID."""
        row = self.db.execute(
            "SELECT * FROM threads WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        
        if not row:
            return None
        
        return self._row_to_thread(row)
    
    def get_active_threads(self, limit: int = 10) -> list[Thread]:
        """Get active threads, ordered by priority."""
        rows = self.db.execute(
            "SELECT * FROM threads "
            "WHERE state = ? "
            "ORDER BY priority DESC, last_update DESC "
            "LIMIT ?",
            (ThreadState.ACTIVE.value, limit),
        ).fetchall()
        
        return [self._row_to_thread(r) for r in rows]
    
    def find_related_thread(self, query: str, session_id: str | None = None) -> Thread | None:
        """Find a thread related to the query."""
        # Simple keyword matching for now
        # Could be enhanced with semantic search
        
        query_lower = query.lower()
        
        # Get active threads
        threads = self.get_active_threads(limit=20)
        
        # Filter by session if provided
        if session_id:
            threads = [t for t in threads if t.session_id == session_id]
        
        # Score threads by relevance
        scored = []
        for thread in threads:
            score = 0.0
            
            # Check title
            if any(word in thread.title.lower() for word in query_lower.split()):
                score += 0.5
            
            # Check description
            if any(word in thread.description.lower() for word in query_lower.split()):
                score += 0.3
            
            # Check tags
            if any(tag.lower() in query_lower for tag in thread.tags):
                score += 0.2
            
            if score > 0:
                scored.append((thread, score))
        
        if not scored:
            return None
        
        # Return highest scoring thread
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]
    
    def update_thread(
        self,
        thread_id: str,
        state: ThreadState | None = None,
        priority: float | None = None,
        context: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> None:
        """Update a thread."""
        now = datetime.now(timezone.utc).isoformat()
        
        updates = {"last_update": now}
        
        if state is not None:
            updates["state"] = state.value
        if priority is not None:
            updates["priority"] = priority
        if context is not None:
            updates["context"] = json.dumps(context)
        if description is not None:
            updates["description"] = description
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [thread_id]
        
        self.db.execute(
            f"UPDATE threads SET {set_clause} WHERE thread_id = ?",
            values,
        )
        self.db.commit()
        
        logger.debug("Updated thread: %s", thread_id)
    
    def add_update(self, update: ThreadUpdate) -> None:
        """Add an update to a thread."""
        self.db.execute(
            "INSERT INTO thread_updates "
            "(thread_id, update_type, content, timestamp, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                update.thread_id,
                update.update_type,
                update.content,
                update.timestamp,
                json.dumps(update.metadata),
            ),
        )
        self.db.commit()
        
        # Update thread's last_update
        self.update_thread(update.thread_id)
    
    def get_thread_updates(self, thread_id: str, limit: int = 10) -> list[ThreadUpdate]:
        """Get updates for a thread."""
        rows = self.db.execute(
            "SELECT * FROM thread_updates "
            "WHERE thread_id = ? "
            "ORDER BY timestamp DESC "
            "LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        
        updates = []
        for r in rows:
            updates.append(
                ThreadUpdate(
                    thread_id=r["thread_id"],
                    update_type=r["update_type"],
                    content=r["content"],
                    timestamp=r["timestamp"],
                    metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                )
            )
        
        return updates
    
    def mark_surfaced(self, thread_id: str) -> None:
        """Mark thread as surfaced to user."""
        now = datetime.now(timezone.utc).isoformat()
        
        self.db.execute(
            "UPDATE threads SET last_surfaced = ? WHERE thread_id = ?",
            (now, thread_id),
        )
        self.db.commit()
    
    def complete_thread(self, thread_id: str) -> None:
        """Mark thread as completed."""
        self.update_thread(thread_id, state=ThreadState.COMPLETED)
        logger.info("Completed thread: %s", thread_id)
    
    def pause_thread(self, thread_id: str) -> None:
        """Pause a thread."""
        self.update_thread(thread_id, state=ThreadState.PAUSED)
        logger.info("Paused thread: %s", thread_id)
    
    def resume_thread(self, thread_id: str) -> None:
        """Resume a paused thread."""
        self.update_thread(thread_id, state=ThreadState.ACTIVE)
        logger.info("Resumed thread: %s", thread_id)
    
    def get_threads_to_surface(self, cooldown_hours: int = 6) -> list[Thread]:
        """Get threads that should be surfaced to user."""
        threads = self.get_active_threads(limit=20)
        
        # Filter threads that should be surfaced
        to_surface = [t for t in threads if t.should_surface(cooldown_hours)]
        
        # Sort by priority
        to_surface.sort(key=lambda t: t.priority, reverse=True)
        
        return to_surface
    
    def cleanup_old_threads(self, days: int = 30) -> int:
        """Clean up old completed threads."""
        from datetime import timedelta
        
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        
        cursor = self.db.execute(
            "DELETE FROM threads "
            "WHERE state = ? AND last_update < ?",
            (ThreadState.COMPLETED.value, cutoff),
        )
        
        deleted = cursor.rowcount
        self.db.commit()
        
        if deleted > 0:
            logger.info("Cleaned up %d old threads", deleted)
        
        return deleted
    
    def _row_to_thread(self, row: sqlite3.Row) -> Thread:
        """Convert database row to Thread object."""
        return Thread(
            thread_id=row["thread_id"],
            type=ThreadType(row["type"]),
            state=ThreadState(row["state"]),
            title=row["title"],
            description=row["description"] or "",
            context=json.loads(row["context"]) if row["context"] else {},
            created_at=row["created_at"],
            last_update=row["last_update"],
            last_surfaced=row["last_surfaced"],
            priority=row["priority"],
            session_id=row["session_id"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
        )
