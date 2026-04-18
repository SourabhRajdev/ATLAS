"""Signal dataclass and signal type definitions for Proactive Intelligence.

A Signal is a detected event that MIGHT deserve user attention.
It does NOT mean the user will be interrupted — the InterruptBudget decides that.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SignalType(str, Enum):
    # Calendar
    CALENDAR_CONFLICT = "calendar_conflict"
    MEETING_NOW = "meeting_now"
    PREP_TIME_NEEDED = "prep_time_needed"
    # Email
    EMAIL_URGENT = "email_urgent"
    EMAIL_ACTION_NEEDED = "email_action_needed"
    # Git
    COMMIT_STALE = "commit_stale"
    BUILD_FAILED = "build_failed"
    PR_REVIEW_NEEDED = "pr_review_needed"
    # System
    BATTERY_CRITICAL = "battery_critical"
    SYSTEM_ERROR_CRITICAL = "system_error_critical"
    # Planning
    DEADLINE_APPROACHING = "deadline_approaching"
    COMMITMENT_DUE = "commitment_due"
    TASK_BLOCKED = "task_blocked"
    # Patterns
    PATTERN_ANOMALY = "pattern_anomaly"
    BEHAVIOR_INSIGHT = "behavior_insight"
    # Generic
    USER_DEFINED = "user_defined"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Map signal type → default priority
_DEFAULT_PRIORITY: dict[str, Priority] = {
    SignalType.MEETING_NOW: Priority.CRITICAL,
    SignalType.BATTERY_CRITICAL: Priority.CRITICAL,
    SignalType.SYSTEM_ERROR_CRITICAL: Priority.CRITICAL,
    SignalType.EMAIL_URGENT: Priority.HIGH,
    SignalType.DEADLINE_APPROACHING: Priority.HIGH,
    SignalType.BUILD_FAILED: Priority.HIGH,
    SignalType.CALENDAR_CONFLICT: Priority.HIGH,
    SignalType.COMMITMENT_DUE: Priority.MEDIUM,
    SignalType.PR_REVIEW_NEEDED: Priority.MEDIUM,
    SignalType.PREP_TIME_NEEDED: Priority.MEDIUM,
    SignalType.EMAIL_ACTION_NEEDED: Priority.MEDIUM,
    SignalType.COMMIT_STALE: Priority.LOW,
    SignalType.PATTERN_ANOMALY: Priority.LOW,
    SignalType.BEHAVIOR_INSIGHT: Priority.LOW,
    SignalType.TASK_BLOCKED: Priority.MEDIUM,
}

# Signal types that ALWAYS interrupt regardless of budget
ALWAYS_INTERRUPT: frozenset[str] = frozenset({
    SignalType.BATTERY_CRITICAL,
    SignalType.MEETING_NOW,
    SignalType.SYSTEM_ERROR_CRITICAL,
})


@dataclass
class Signal:
    type: str
    source: str
    payload: dict
    priority: float = 0.5       # 0.0-1.0
    urgency: float = 0.5        # 0.0-1.0 (time-sensitive component)
    confidence: float = 0.8     # how sure we are this needs attention
    expiry: float = 0.0         # unix timestamp, 0 = never expires
    suggested_action: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    surfaced_at: float | None = None
    outcome: str | None = None  # "acted"|"dismissed"|"ignored"|"batched"
    priority_boost: float = 0.0  # learned adjustment

    @classmethod
    def create(
        cls,
        type: str,
        source: str,
        payload: dict,
        priority: Priority | None = None,
        urgency: float = 0.5,
        confidence: float = 0.8,
        ttl_seconds: float = 4 * 3600,  # default 4h expiry
        suggested_action: str | None = None,
    ) -> "Signal":
        prio_enum = priority or _DEFAULT_PRIORITY.get(type, Priority.MEDIUM)
        prio_value = {
            Priority.CRITICAL: 0.95,
            Priority.HIGH: 0.75,
            Priority.MEDIUM: 0.50,
            Priority.LOW: 0.25,
        }[prio_enum]

        expiry = time.time() + ttl_seconds if ttl_seconds > 0 else 0.0

        return cls(
            type=type,
            source=source,
            payload=payload,
            priority=prio_value,
            urgency=urgency,
            confidence=confidence,
            expiry=expiry,
            suggested_action=suggested_action,
        )

    def is_expired(self) -> bool:
        if self.expiry <= 0:
            return False
        return time.time() > self.expiry

    def effective_priority(self) -> float:
        return min(1.0, max(0.0, self.priority + self.priority_boost))

    def priority_label(self) -> Priority:
        p = self.effective_priority()
        if p >= 0.85:
            return Priority.CRITICAL
        if p >= 0.65:
            return Priority.HIGH
        if p >= 0.40:
            return Priority.MEDIUM
        return Priority.LOW
