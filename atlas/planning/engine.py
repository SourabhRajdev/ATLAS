"""PlanningEngine — high-level API for long-horizon planning."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from atlas.planning.inference import InferenceEngine
from atlas.planning.models import Goal, GoalStatus, Priority, Task, TaskStatus
from atlas.planning.replanner import WeeklyReplanner
from atlas.planning.store import PlanningStore

logger = logging.getLogger("atlas.planning.engine")


class PlanningEngine:
    def __init__(self, data_dir: Path) -> None:
        self._store = PlanningStore(data_dir / "planning.db")
        self._inference = InferenceEngine()
        self._replanner = WeeklyReplanner(self._store)

    # ── Goals ─────────────────────────────────────────────────────────────

    def create_goal(
        self,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        due_date: float | None = None,
        success_criteria: str = "",
        tags: list[str] | None = None,
        auto_decompose: bool = True,
    ) -> tuple[Goal, list[Task]]:
        """Create a goal and optionally generate initial tasks."""
        goal = Goal.create(
            title=title,
            description=description,
            priority=priority,
            due_date=due_date,
            success_criteria=success_criteria,
            tags=tags,
        )
        self._store.save_goal(goal)

        tasks: list[Task] = []
        if auto_decompose:
            tasks = self._inference.decompose(goal)
            for task in tasks:
                self._store.save_task(task)

        logger.info("Created goal '%s' with %d tasks", title, len(tasks))
        return goal, tasks

    def get_goal(self, goal_id: str) -> Goal | None:
        return self._store.get_goal(goal_id)

    def get_active_goals(self) -> list[Goal]:
        return self._store.get_active_goals()

    def complete_goal(self, goal_id: str) -> bool:
        goal = self._store.get_goal(goal_id)
        if not goal:
            return False
        goal.status = GoalStatus.COMPLETED
        goal.progress = 1.0
        self._store.save_goal(goal)
        return True

    def abandon_goal(self, goal_id: str) -> bool:
        goal = self._store.get_goal(goal_id)
        if not goal:
            return False
        goal.status = GoalStatus.ABANDONED
        self._store.save_goal(goal)
        return True

    # ── Tasks ─────────────────────────────────────────────────────────────

    def add_task(self, goal_id: str, title: str, **kwargs) -> Task | None:
        if not self._store.get_goal(goal_id):
            logger.warning("add_task: goal %s not found", goal_id)
            return None
        task = Task.create(goal_id=goal_id, title=title, **kwargs)
        self._store.save_task(task)
        return task

    def complete_task(self, task_id: str, actual_minutes: int = 0) -> bool:
        return self._store.complete_task(task_id, actual_minutes)

    def get_tasks_for_goal(self, goal_id: str) -> list[Task]:
        return self._store.get_tasks_for_goal(goal_id)

    def get_next_actions(self, goal_id: str | None = None) -> list[Task]:
        """Return top unblocked tasks to work on next."""
        if goal_id:
            goal = self._store.get_goal(goal_id)
            if not goal:
                return []
            tasks = self._store.get_tasks_for_goal(goal_id)
            return self._inference.suggest_next(goal, tasks)

        # Across all active goals
        all_suggestions: list[Task] = []
        for goal in self._store.get_active_goals():
            tasks = self._store.get_tasks_for_goal(goal.id)
            suggestions = self._inference.suggest_next(goal, tasks)
            all_suggestions.extend(suggestions[:2])  # max 2 per goal

        priority_order = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3}
        all_suggestions.sort(key=lambda t: (priority_order.get(t.priority, 2), t.estimated_minutes))
        return all_suggestions[:7]

    # ── Weekly Planning ───────────────────────────────────────────────────

    def run_weekly_plan(self) -> str:
        return self._replanner.run()

    def current_week_status(self) -> str:
        return self._replanner.get_current_week_briefing()

    # ── Health ────────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        try:
            goals = self._store.get_active_goals()
            pending = self._store.get_pending_tasks(limit=5)
            return {
                "status": "healthy",
                "active_goals": len(goals),
                "pending_tasks": len(pending),
            }
        except Exception as e:
            return {"status": "down", "error": str(e)}

    def close(self) -> None:
        self._store.close()
