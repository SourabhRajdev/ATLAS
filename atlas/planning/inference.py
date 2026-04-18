"""InferenceEngine — decomposes goals into tasks using pattern matching.

No LLM calls. Uses keyword/pattern heuristics to suggest initial task lists
for common goal types. Callers can then refine via the planning interface.

Goal types detected: project, learning, habit, fitness, writing, career, travel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from atlas.planning.models import Goal, Priority, Task

_HOUR = 60
_HALF_DAY = 4 * _HOUR


@dataclass
class TaskTemplate:
    title: str
    description: str = ""
    estimated_minutes: int = 30
    suggested_action: str = ""
    depends_on_index: list[int] = None  # indices into template list

    def __post_init__(self):
        if self.depends_on_index is None:
            self.depends_on_index = []


_TEMPLATES: dict[str, list[TaskTemplate]] = {
    "project": [
        TaskTemplate("Define scope and success criteria", estimated_minutes=60,
                     suggested_action="Write a one-page spec covering goals, non-goals, and done criteria"),
        TaskTemplate("Break down into milestones", estimated_minutes=30, depends_on_index=[0]),
        TaskTemplate("Set up development environment", estimated_minutes=30),
        TaskTemplate("Build MVP / first working version", estimated_minutes=_HALF_DAY, depends_on_index=[1, 2]),
        TaskTemplate("Review and iterate", estimated_minutes=_HOUR, depends_on_index=[3]),
        TaskTemplate("Ship / publish / present", estimated_minutes=_HOUR, depends_on_index=[4]),
    ],
    "learning": [
        TaskTemplate("Curate learning resources", estimated_minutes=30,
                     suggested_action="Find 2-3 books, courses, or tutorials on the topic"),
        TaskTemplate("Study fundamentals (week 1)", estimated_minutes=5 * _HOUR, depends_on_index=[0]),
        TaskTemplate("Apply with a small practice project", estimated_minutes=3 * _HOUR, depends_on_index=[1]),
        TaskTemplate("Review and fill gaps", estimated_minutes=2 * _HOUR, depends_on_index=[2]),
        TaskTemplate("Build something real", estimated_minutes=_HALF_DAY, depends_on_index=[3]),
    ],
    "writing": [
        TaskTemplate("Outline structure and key points", estimated_minutes=30),
        TaskTemplate("Write first draft", estimated_minutes=3 * _HOUR, depends_on_index=[0]),
        TaskTemplate("Revise and edit", estimated_minutes=2 * _HOUR, depends_on_index=[1]),
        TaskTemplate("Get feedback", estimated_minutes=30, depends_on_index=[2]),
        TaskTemplate("Final polish and publish", estimated_minutes=_HOUR, depends_on_index=[3]),
    ],
    "fitness": [
        TaskTemplate("Set baseline measurement", estimated_minutes=30,
                     suggested_action="Record current weight/time/reps as baseline"),
        TaskTemplate("Plan workout schedule (week 1)", estimated_minutes=20),
        TaskTemplate("Week 1 workouts", estimated_minutes=5 * _HOUR),
        TaskTemplate("Week 2 workouts", estimated_minutes=5 * _HOUR, depends_on_index=[2]),
        TaskTemplate("Week 3 workouts", estimated_minutes=5 * _HOUR, depends_on_index=[3]),
        TaskTemplate("Week 4 workouts + assessment", estimated_minutes=5 * _HOUR, depends_on_index=[4]),
    ],
    "habit": [
        TaskTemplate("Define exact habit (what / when / where)", estimated_minutes=15,
                     suggested_action="Write out: 'I will [action] at [time] in [location]'"),
        TaskTemplate("Week 1: daily practice", estimated_minutes=7 * 15),
        TaskTemplate("Week 2: daily practice", estimated_minutes=7 * 15, depends_on_index=[1]),
        TaskTemplate("Week 3: daily practice + review", estimated_minutes=7 * 15 + 30, depends_on_index=[2]),
        TaskTemplate("Week 4: daily practice + lock in cue", estimated_minutes=7 * 15, depends_on_index=[3]),
    ],
    "career": [
        TaskTemplate("Audit current skills and gaps", estimated_minutes=_HOUR),
        TaskTemplate("Research target roles / opportunities", estimated_minutes=2 * _HOUR, depends_on_index=[0]),
        TaskTemplate("Update resume and portfolio", estimated_minutes=3 * _HOUR, depends_on_index=[1]),
        TaskTemplate("Reach out to 5 relevant contacts", estimated_minutes=2 * _HOUR, depends_on_index=[1]),
        TaskTemplate("Apply / pitch / negotiate", estimated_minutes=2 * _HOUR, depends_on_index=[2, 3]),
    ],
    "travel": [
        TaskTemplate("Research destination and dates", estimated_minutes=_HOUR),
        TaskTemplate("Book flights", estimated_minutes=_HOUR, depends_on_index=[0]),
        TaskTemplate("Book accommodation", estimated_minutes=30, depends_on_index=[1]),
        TaskTemplate("Plan daily itinerary", estimated_minutes=_HOUR, depends_on_index=[0]),
        TaskTemplate("Pack and prepare", estimated_minutes=_HOUR),
    ],
}

_KEYWORD_MAP: list[tuple[list[str], str]] = [
    (["build", "develop", "create", "launch", "ship", "implement", "app", "website", "tool", "api"], "project"),
    (["learn", "study", "understand", "master", "course", "tutorial", "book", "skill"], "learning"),
    (["write", "blog", "article", "essay", "book", "newsletter", "draft", "publish"], "writing"),
    (["run", "gym", "workout", "exercise", "fitness", "weight", "marathon", "strength"], "fitness"),
    (["habit", "routine", "daily", "meditate", "journal", "practice", "streak"], "habit"),
    (["job", "career", "promotion", "salary", "interview", "resume", "cv", "network"], "career"),
    (["travel", "trip", "vacation", "visit", "flight", "hotel", "country", "city"], "travel"),
]


def detect_goal_type(goal: Goal) -> str:
    text = (goal.title + " " + goal.description).lower()
    scores: dict[str, int] = {}
    for keywords, gtype in _KEYWORD_MAP:
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[gtype] = score
    if not scores:
        return "project"  # default
    return max(scores, key=lambda k: scores[k])


class InferenceEngine:
    """Decomposes goals into ordered task lists without any LLM calls."""

    def decompose(self, goal: Goal) -> list[Task]:
        """Generate initial task list for a goal."""
        goal_type = detect_goal_type(goal)
        templates = _TEMPLATES.get(goal_type, _TEMPLATES["project"])
        tasks: list[Task] = []

        for tmpl in templates:
            task = Task.create(
                goal_id=goal.id,
                title=tmpl.title,
                description=tmpl.description,
                priority=goal.priority,
                estimated_minutes=tmpl.estimated_minutes,
                suggested_action=tmpl.suggested_action,
            )
            tasks.append(task)

        # Wire up dependencies by index
        for i, (tmpl, task) in enumerate(zip(templates, tasks)):
            task.depends_on = [tasks[j].id for j in tmpl.depends_on_index if j < len(tasks)]

        return tasks

    def suggest_next(self, goal: Goal, all_tasks: list[Task]) -> list[Task]:
        """Return tasks that are unblocked and ready to work on."""
        completed_ids = {t.id for t in all_tasks if t.status.value == "completed"}
        pending = [
            t for t in all_tasks
            if t.status.value in ("pending", "in_progress")
            and not t.is_blocked(completed_ids)
        ]
        # Sort by priority then estimated_minutes ascending (quick wins first)
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        pending.sort(key=lambda t: (priority_order.get(t.priority.value, 2), t.estimated_minutes))
        return pending[:5]

    def estimate_completion(self, goal: Goal, tasks: list[Task]) -> float | None:
        """Estimate Unix timestamp when goal will complete, given available time."""
        pending = [t for t in tasks if t.status.value not in ("completed", "skipped")]
        total_minutes = sum(t.estimated_minutes for t in pending)
        # Assume 2h of focused work per day
        days_needed = total_minutes / 120
        if goal.due_date and days_needed > (goal.due_date - __import__("time").time()) / 86400:
            return None  # will miss deadline
        import time
        return time.time() + days_needed * 86400
