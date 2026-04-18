"""WeeklyReplanner — runs every Sunday night to prepare next week's plan.

Logic:
1. Carry over incomplete high-priority tasks from current week
2. Select new tasks from active goals, respecting capacity
3. Detect goals at risk (overdue, no progress in 2+ weeks)
4. Emit a weekly briefing string
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from atlas.planning.models import Goal, GoalStatus, Priority, Task, TaskStatus, WeekPlan
from atlas.planning.store import PlanningStore

logger = logging.getLogger("atlas.planning.replanner")

WEEKLY_CAPACITY_MINUTES = 600  # 10 hours default
STALE_GOAL_DAYS = 14           # no progress in 2 weeks → at risk
MAX_TASKS_PER_WEEK = 10


def _current_week() -> tuple[int, int]:
    """Return (week_number, year) for the current ISO week."""
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return iso[1], iso[0]


def _next_week() -> tuple[int, int]:
    week, year = _current_week()
    if week >= 52:
        return 1, year + 1
    return week + 1, year


class WeeklyReplanner:
    def __init__(self, store: PlanningStore) -> None:
        self._store = store

    def run(self) -> str:
        """Execute weekly replanning. Returns a briefing string."""
        next_week_num, next_year = _next_week()
        week_key = f"{next_year}-W{next_week_num:02d}"

        active_goals = self._store.get_active_goals()
        if not active_goals:
            return "No active goals. Consider creating some with /plan goal."

        # Gather all pending tasks
        all_pending: list[Task] = []
        for goal in active_goals:
            tasks = self._store.get_tasks_for_goal(goal.id)
            completed_ids = {t.id for t in tasks if t.status == TaskStatus.COMPLETED}
            pending = [
                t for t in tasks
                if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
                and not t.is_blocked(completed_ids)
            ]
            all_pending.extend(pending)

        # Sort: priority then due_date
        priority_order = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3}
        all_pending.sort(key=lambda t: (
            priority_order.get(t.priority, 2),
            t.due_date or float("inf")
        ))

        # Fill week up to capacity
        chosen: list[Task] = []
        capacity_used = 0
        for task in all_pending:
            if len(chosen) >= MAX_TASKS_PER_WEEK:
                break
            if capacity_used + task.estimated_minutes > WEEKLY_CAPACITY_MINUTES:
                if task.priority in (Priority.CRITICAL, Priority.HIGH):
                    chosen.append(task)  # always include critical/high regardless of capacity
                continue
            chosen.append(task)
            capacity_used += task.estimated_minutes

        # Update week_number on chosen tasks
        for task in chosen:
            task.week_number = next_week_num
            self._store.save_task(task)

        # Save the week plan
        plan = WeekPlan(
            week_number=next_week_num,
            year=next_year,
            goal_ids=list({t.goal_id for t in chosen}),
            task_ids=[t.id for t in chosen],
            capacity_minutes=WEEKLY_CAPACITY_MINUTES,
        )
        self._store.save_week_plan(plan)

        # Detect at-risk goals
        at_risk = self._detect_at_risk(active_goals)

        return self._format_briefing(plan, chosen, active_goals, at_risk)

    def _detect_at_risk(self, goals: list[Goal]) -> list[Goal]:
        now = time.time()
        at_risk: list[Goal] = []
        for goal in goals:
            if goal.is_overdue():
                at_risk.append(goal)
                continue
            tasks = self._store.get_tasks_for_goal(goal.id)
            if not tasks:
                continue
            last_completed = max(
                (t.completed_at for t in tasks if t.completed_at), default=goal.created_at
            )
            if now - last_completed > STALE_GOAL_DAYS * 86400:
                at_risk.append(goal)
        return at_risk

    def _format_briefing(
        self,
        plan: WeekPlan,
        tasks: list[Task],
        goals: list[Goal],
        at_risk: list[Goal],
    ) -> str:
        lines = [f"Weekly Plan — {plan.week_key}", ""]

        goal_map = {g.id: g for g in goals}

        if tasks:
            lines.append(f"Tasks this week ({len(tasks)}):")
            for task in tasks:
                goal_title = goal_map.get(task.goal_id, None)
                g_label = f" [{goal_title.title}]" if goal_title else ""
                lines.append(f"  • {task.title}{g_label} (~{task.estimated_minutes}min)")
        else:
            lines.append("No tasks scheduled — all goals are either completed or blocked.")

        if at_risk:
            lines.append("")
            lines.append(f"At-risk goals ({len(at_risk)}):")
            for g in at_risk:
                reason = "overdue" if g.is_overdue() else "no progress in 2+ weeks"
                lines.append(f"  ⚠ {g.title} ({reason})")

        lines.append("")
        total_h = sum(t.estimated_minutes for t in tasks) / 60
        lines.append(f"Estimated effort: {total_h:.1f}h / {plan.capacity_minutes // 60}h capacity")

        return "\n".join(lines)

    def get_current_week_briefing(self) -> str:
        """Return briefing for the current (already planned) week."""
        week_num, year = _current_week()
        week_key = f"{year}-W{week_num:02d}"
        plan = self._store.get_week_plan(week_key)
        if not plan:
            return f"No plan found for {week_key}. Run weekly replanner first."

        tasks = self._store.get_tasks_for_week(week_num)
        done = [t for t in tasks if t.status == TaskStatus.COMPLETED]
        pending = [t for t in tasks if t.status != TaskStatus.COMPLETED]

        lines = [f"Current week: {week_key}", ""]
        lines.append(f"Progress: {len(done)}/{len(tasks)} tasks done")
        if pending:
            lines.append("Remaining:")
            for t in pending[:5]:
                lines.append(f"  • {t.title}")
        return "\n".join(lines)
