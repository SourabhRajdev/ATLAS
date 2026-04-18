"""PlanningStore — SQLite persistence for goals and tasks."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from atlas.planning.models import Goal, GoalStatus, Priority, Task, TaskStatus, WeekPlan
from atlas.planning.schema import SCHEMA

logger = logging.getLogger("atlas.planning.store")


def _row_to_goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=GoalStatus(row["status"]),
        priority=Priority(row["priority"]),
        due_date=row["due_date"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        success_criteria=row["success_criteria"],
        tags=json.loads(row["tags"] or "[]"),
        progress=row["progress"],
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        goal_id=row["goal_id"],
        title=row["title"],
        description=row["description"],
        status=TaskStatus(row["status"]),
        priority=Priority(row["priority"]),
        estimated_minutes=row["estimated_minutes"],
        actual_minutes=row["actual_minutes"],
        due_date=row["due_date"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        depends_on=json.loads(row["depends_on"] or "[]"),
        suggested_action=row["suggested_action"] or "",
        week_number=row["week_number"],
    )


class PlanningStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── Goals ─────────────────────────────────────────────────────────────

    def save_goal(self, goal: Goal) -> None:
        goal.updated_at = time.time()
        self._conn.execute("""
            INSERT INTO goals (id, title, description, status, priority, due_date,
                created_at, updated_at, success_criteria, tags, progress)
            VALUES (:id, :title, :description, :status, :priority, :due_date,
                :created_at, :updated_at, :success_criteria, :tags, :progress)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, description=excluded.description,
                status=excluded.status, priority=excluded.priority,
                due_date=excluded.due_date, updated_at=excluded.updated_at,
                success_criteria=excluded.success_criteria, tags=excluded.tags,
                progress=excluded.progress
        """, {
            "id": goal.id, "title": goal.title, "description": goal.description,
            "status": goal.status.value, "priority": goal.priority.value,
            "due_date": goal.due_date, "created_at": goal.created_at,
            "updated_at": goal.updated_at, "success_criteria": goal.success_criteria,
            "tags": json.dumps(goal.tags), "progress": goal.progress,
        })
        self._conn.commit()
        self._log_event("goal_saved", goal.id, {"status": goal.status.value})

    def get_goal(self, goal_id: str) -> Goal | None:
        row = self._conn.execute(
            "SELECT * FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()
        return _row_to_goal(row) if row else None

    def get_active_goals(self) -> list[Goal]:
        rows = self._conn.execute(
            "SELECT * FROM goals WHERE status = 'active' ORDER BY priority, due_date NULLS LAST"
        ).fetchall()
        return [_row_to_goal(r) for r in rows]

    def get_all_goals(self) -> list[Goal]:
        rows = self._conn.execute(
            "SELECT * FROM goals ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_goal(r) for r in rows]

    def update_goal_progress(self, goal_id: str) -> float:
        """Recalculate and store goal progress from task completion ratio."""
        total = self._conn.execute(
            "SELECT COUNT(*) as n FROM tasks WHERE goal_id = ?", (goal_id,)
        ).fetchone()["n"]
        if total == 0:
            return 0.0
        done = self._conn.execute(
            "SELECT COUNT(*) as n FROM tasks WHERE goal_id = ? AND status = 'completed'",
            (goal_id,)
        ).fetchone()["n"]
        progress = done / total
        self._conn.execute(
            "UPDATE goals SET progress = ?, updated_at = ? WHERE id = ?",
            (progress, time.time(), goal_id)
        )
        self._conn.commit()
        return progress

    # ── Tasks ─────────────────────────────────────────────────────────────

    def save_task(self, task: Task) -> None:
        task.updated_at = time.time()
        self._conn.execute("""
            INSERT INTO tasks (id, goal_id, title, description, status, priority,
                estimated_minutes, actual_minutes, due_date, created_at, updated_at,
                completed_at, depends_on, suggested_action, week_number)
            VALUES (:id, :goal_id, :title, :description, :status, :priority,
                :estimated_minutes, :actual_minutes, :due_date, :created_at, :updated_at,
                :completed_at, :depends_on, :suggested_action, :week_number)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, description=excluded.description,
                status=excluded.status, priority=excluded.priority,
                estimated_minutes=excluded.estimated_minutes,
                actual_minutes=excluded.actual_minutes, due_date=excluded.due_date,
                updated_at=excluded.updated_at, completed_at=excluded.completed_at,
                depends_on=excluded.depends_on, suggested_action=excluded.suggested_action,
                week_number=excluded.week_number
        """, {
            "id": task.id, "goal_id": task.goal_id, "title": task.title,
            "description": task.description, "status": task.status.value,
            "priority": task.priority.value, "estimated_minutes": task.estimated_minutes,
            "actual_minutes": task.actual_minutes, "due_date": task.due_date,
            "created_at": task.created_at, "updated_at": task.updated_at,
            "completed_at": task.completed_at, "depends_on": json.dumps(task.depends_on),
            "suggested_action": task.suggested_action, "week_number": task.week_number,
        })
        self._conn.commit()
        self._log_event("task_saved", task.id, {"status": task.status.value, "goal_id": task.goal_id})

    def get_task(self, task_id: str) -> Task | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _row_to_task(row) if row else None

    def get_tasks_for_goal(self, goal_id: str) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE goal_id = ? ORDER BY priority, due_date NULLS LAST",
            (goal_id,)
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def get_pending_tasks(self, limit: int = 50) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status IN ('pending', 'in_progress') "
            "ORDER BY priority, due_date NULLS LAST LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def get_tasks_for_week(self, week_number: int) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE week_number = ? ORDER BY priority",
            (week_number,)
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def complete_task(self, task_id: str, actual_minutes: int = 0) -> bool:
        now = time.time()
        cursor = self._conn.execute(
            "UPDATE tasks SET status='completed', completed_at=?, actual_minutes=?, updated_at=? "
            "WHERE id = ? AND status != 'completed'",
            (now, actual_minutes, now, task_id)
        )
        self._conn.commit()
        if cursor.rowcount > 0:
            task = self.get_task(task_id)
            if task:
                self.update_goal_progress(task.goal_id)
            self._log_event("task_completed", task_id, {"actual_minutes": actual_minutes})
            return True
        return False

    # ── Week Plans ────────────────────────────────────────────────────────

    def save_week_plan(self, plan: WeekPlan) -> None:
        self._conn.execute("""
            INSERT INTO week_plans (week_key, week_number, year, goal_ids, task_ids,
                capacity_minutes, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_key) DO UPDATE SET
                goal_ids=excluded.goal_ids, task_ids=excluded.task_ids,
                capacity_minutes=excluded.capacity_minutes, notes=excluded.notes
        """, (plan.week_key, plan.week_number, plan.year,
              json.dumps(plan.goal_ids), json.dumps(plan.task_ids),
              plan.capacity_minutes, plan.notes, plan.created_at))
        self._conn.commit()

    def get_week_plan(self, week_key: str) -> WeekPlan | None:
        row = self._conn.execute(
            "SELECT * FROM week_plans WHERE week_key = ?", (week_key,)
        ).fetchone()
        if not row:
            return None
        return WeekPlan(
            week_number=row["week_number"],
            year=row["year"],
            goal_ids=json.loads(row["goal_ids"] or "[]"),
            task_ids=json.loads(row["task_ids"] or "[]"),
            capacity_minutes=row["capacity_minutes"],
            notes=row["notes"],
            created_at=row["created_at"],
        )

    # ── Events ────────────────────────────────────────────────────────────

    def _log_event(self, event_type: str, entity_id: str | None, payload: dict) -> None:
        self._conn.execute(
            "INSERT INTO planning_events (event_type, entity_id, payload, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (event_type, entity_id, json.dumps(payload), time.time())
        )

    def close(self) -> None:
        self._conn.close()
