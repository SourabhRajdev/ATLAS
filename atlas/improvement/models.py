"""Self-improvement domain models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SignalKind(str, Enum):
    TASK_DURATION_OVERRUN  = "task_duration_overrun"   # took 2x+ estimated
    TASK_REPEATED_FAILURE  = "task_repeated_failure"   # same task failed 3+ times
    TOOL_ERROR_SPIKE       = "tool_error_spike"        # tool failing >20% of calls
    USER_CORRECTION        = "user_correction"         # user edited Claude's output
    USER_APPROVAL          = "user_approval"           # user explicitly approved
    POSITIVE_FEEDBACK      = "positive_feedback"       # user said "good", "perfect", etc.
    NEGATIVE_FEEDBACK      = "negative_feedback"       # user said "wrong", "no", etc.
    RESPONSE_TOO_LONG      = "response_too_long"       # >2000 tokens when <500 expected
    CONTEXT_LOST           = "context_lost"            # user had to re-explain something
    GOAL_COMPLETED         = "goal_completed"          # goal hit done state
    GOAL_ABANDONED         = "goal_abandoned"          # user abandoned a goal


class ImpactLevel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL  = "neutral"


@dataclass
class QualitySignal:
    id: str
    kind: SignalKind
    impact: ImpactLevel
    context: str          # what was happening (task name, tool name, etc.)
    detail: str = ""
    value: float = 0.0    # numeric measurement when applicable
    recorded_at: float = field(default_factory=time.time)
    session_id: str = ""

    @classmethod
    def create(
        cls,
        kind: SignalKind,
        impact: ImpactLevel,
        context: str,
        detail: str = "",
        value: float = 0.0,
        session_id: str = "",
    ) -> "QualitySignal":
        return cls(
            id=str(uuid.uuid4()),
            kind=kind,
            impact=impact,
            context=context,
            detail=detail,
            value=value,
            session_id=session_id,
        )


@dataclass
class BehaviorPattern:
    """Aggregated pattern identified from quality signals."""
    pattern_type: str      # matches SignalKind value
    frequency: int         # occurrences in rolling window
    impact: ImpactLevel
    contexts: list[str]    # which contexts triggered it
    first_seen: float
    last_seen: float
    recommendation: str = ""

    @property
    def is_concerning(self) -> bool:
        return self.impact == ImpactLevel.NEGATIVE and self.frequency >= 3


@dataclass
class WeeklyReport:
    week_key: str
    generated_at: float
    total_signals: int
    positive_count: int
    negative_count: int
    patterns: list[BehaviorPattern]
    recommendations: list[str]
    summary: str

    @property
    def health_score(self) -> float:
        """0–1 score: ratio of positive to total signals."""
        total = self.positive_count + self.negative_count
        if total == 0:
            return 1.0
        return self.positive_count / total
