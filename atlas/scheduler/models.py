"""Scheduler task models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


class ScheduledTask(BaseModel):
    """A scheduled task."""
    id: str = Field(default_factory=_uid)
    name: str
    task_type: str  # tool | workflow
    target: str  # tool name or workflow name
    params: dict[str, Any] = {}
    schedule: str | None = None  # cron expression or None for one-time
    next_run: str  # ISO timestamp
    last_run: str | None = None
    enabled: bool = True
    created_at: str = Field(default_factory=_now)
