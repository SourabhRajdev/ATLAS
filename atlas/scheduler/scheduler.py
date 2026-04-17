"""Simple SQLite-based task scheduler."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from atlas.scheduler.models import ScheduledTask

logger = logging.getLogger("atlas.scheduler")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    task_type   TEXT NOT NULL,
    target      TEXT NOT NULL,
    params      TEXT,
    schedule    TEXT,
    next_run    TEXT NOT NULL,
    last_run    TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_next_run ON scheduled_tasks(next_run, enabled);
"""


class Scheduler:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.running = False

    def close(self) -> None:
        self.db.close()

    def add_task(self, task: ScheduledTask) -> None:
        """Add a scheduled task."""
        self.db.execute(
            "INSERT INTO scheduled_tasks "
            "(id, name, task_type, target, params, schedule, next_run, last_run, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task.id, task.name, task.task_type, task.target,
             json.dumps(task.params), task.schedule, task.next_run,
             task.last_run, task.enabled, task.created_at),
        )
        self.db.commit()
        logger.info("Added task: %s (next run: %s)", task.name, task.next_run)

    def get_due_tasks(self) -> list[ScheduledTask]:
        """Get tasks that are due to run."""
        now = datetime.now(timezone.utc).isoformat()
        rows = self.db.execute(
            "SELECT * FROM scheduled_tasks "
            "WHERE enabled = 1 AND next_run <= ? "
            "ORDER BY next_run",
            (now,),
        ).fetchall()

        tasks = []
        for r in rows:
            task = ScheduledTask(
                id=r["id"],
                name=r["name"],
                task_type=r["task_type"],
                target=r["target"],
                params=json.loads(r["params"]) if r["params"] else {},
                schedule=r["schedule"],
                next_run=r["next_run"],
                last_run=r["last_run"],
                enabled=bool(r["enabled"]),
                created_at=r["created_at"],
            )
            tasks.append(task)

        return tasks

    def update_task_after_run(self, task_id: str, success: bool) -> None:
        """Update task after execution."""
        now = datetime.now(timezone.utc).isoformat()
        
        # Get task to check if it's recurring
        row = self.db.execute(
            "SELECT schedule FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

        if not row:
            return

        schedule = row["schedule"]
        
        if schedule:
            # Recurring task - calculate next run
            next_run = self._calculate_next_run(schedule)
            self.db.execute(
                "UPDATE scheduled_tasks SET last_run = ?, next_run = ? WHERE id = ?",
                (now, next_run, task_id),
            )
        else:
            # One-time task - disable it
            self.db.execute(
                "UPDATE scheduled_tasks SET last_run = ?, enabled = 0 WHERE id = ?",
                (now, task_id),
            )

        self.db.commit()

    def _calculate_next_run(self, schedule: str) -> str:
        """Calculate next run time from schedule string."""
        # Simple schedule format: "every Xm" (minutes), "every Xh" (hours), "every Xd" (days)
        now = datetime.now(timezone.utc)
        
        if schedule.startswith("every "):
            parts = schedule.split()
            if len(parts) == 2:
                value = parts[1]
                if value.endswith("m"):
                    minutes = int(value[:-1])
                    next_run = now + timedelta(minutes=minutes)
                elif value.endswith("h"):
                    hours = int(value[:-1])
                    next_run = now + timedelta(hours=hours)
                elif value.endswith("d"):
                    days = int(value[:-1])
                    next_run = now + timedelta(days=days)
                else:
                    next_run = now + timedelta(hours=1)  # default
                
                return next_run.isoformat()

        # Default: 1 hour
        return (now + timedelta(hours=1)).isoformat()

    def list_tasks(self) -> list[dict]:
        """List all scheduled tasks."""
        rows = self.db.execute(
            "SELECT * FROM scheduled_tasks ORDER BY next_run"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_task(self, task_id: str) -> None:
        """Delete a scheduled task."""
        self.db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        self.db.commit()
