"""Planning domain models — Goals, Tasks, Dependencies."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Goal:
    id: str
    title: str
    description: str = ""
    status: GoalStatus = GoalStatus.ACTIVE
    priority: Priority = Priority.MEDIUM
    due_date: Optional[float] = None      # Unix timestamp, None = no deadline
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    success_criteria: str = ""
    tags: list[str] = field(default_factory=list)
    progress: float = 0.0                 # 0.0 → 1.0

    @classmethod
    def create(
        cls,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        due_date: float | None = None,
        success_criteria: str = "",
        tags: list[str] | None = None,
    ) -> "Goal":
        return cls(
            id=str(uuid.uuid4()),
            title=title,
            description=description,
            priority=priority,
            due_date=due_date,
            success_criteria=success_criteria,
            tags=tags or [],
        )

    def is_overdue(self) -> bool:
        return self.due_date is not None and time.time() > self.due_date

    def days_until_due(self) -> float | None:
        if self.due_date is None:
            return None
        return (self.due_date - time.time()) / 86400


@dataclass
class Task:
    id: str
    goal_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: Priority = Priority.MEDIUM
    estimated_minutes: int = 30
    actual_minutes: int = 0
    due_date: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    depends_on: list[str] = field(default_factory=list)   # list of task IDs
    suggested_action: str = ""
    week_number: int = 0  # ISO week number this task was planned for

    @classmethod
    def create(
        cls,
        goal_id: str,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        estimated_minutes: int = 30,
        due_date: float | None = None,
        depends_on: list[str] | None = None,
        suggested_action: str = "",
    ) -> "Task":
        return cls(
            id=str(uuid.uuid4()),
            goal_id=goal_id,
            title=title,
            description=description,
            priority=priority,
            estimated_minutes=estimated_minutes,
            due_date=due_date,
            depends_on=depends_on or [],
            suggested_action=suggested_action,
        )

    def is_blocked(self, completed_ids: set[str]) -> bool:
        return any(dep not in completed_ids for dep in self.depends_on)


@dataclass
class WeekPlan:
    week_number: int
    year: int
    goal_ids: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    capacity_minutes: int = 600           # 10h default per week
    notes: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def week_key(self) -> str:
        return f"{self.year}-W{self.week_number:02d}"
