"""Autonomy data types — unified for both simple (Reasoner) and advanced (AutonomyLoop) pipelines."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Priority + ActionDecision — used by AutonomyLoop / AttentionSystem
# ---------------------------------------------------------------------------

class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionDecision(str, Enum):
    NOTIFY = "notify"
    ACT = "act"
    IGNORE = "ignore"


# ---------------------------------------------------------------------------
# Signal — unified model for both old (source/kind/payload) and
#           new (type/description/data) field conventions.
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    source: str                             # "calendar", "scheduler", "memory_analyzer", ...

    # Old fields (Reasoner-based simple loop)
    kind: str = ""                          # "meeting_t5", "battery_low", ...
    payload: dict[str, Any] = field(default_factory=dict)

    # New fields (AutonomyLoop / AttentionSystem / SignalScorer)
    type: str = ""                          # "scheduled_task", "anomaly", "suggestion", ...
    description: str = ""                   # human-readable sentence
    data: dict[str, Any] = field(default_factory=dict)

    detected_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        # Keep old/new fields in sync so both pipelines can use the same object.
        if self.type and not self.kind:
            self.kind = self.type
        if self.kind and not self.type:
            self.type = self.kind
        if self.payload and not self.data:
            self.data = self.payload
        if self.data and not self.payload:
            self.payload = self.data
        # Auto-generate description if missing
        if not self.description:
            self.description = f"{self.type or self.kind} from {self.source}"


# ---------------------------------------------------------------------------
# Confidence + Suggestion — used by Reasoner (simple loop)
# ---------------------------------------------------------------------------

@dataclass
class Confidence:
    """Structured score — every field in [0,1]. Product is final weight."""
    signal_quality: float = 0.0
    action_correctness: float = 0.0
    user_wants_this: float = 0.0
    reversibility: float = 1.0

    @property
    def score(self) -> float:
        return (
            self.signal_quality
            * self.action_correctness
            * self.user_wants_this
            * max(0.1, self.reversibility)
        )


@dataclass
class Suggestion:
    signal: Signal
    title: str
    rationale: str
    action_kind: str = ""
    action_params: dict[str, Any] = field(default_factory=dict)
    confidence: Confidence = field(default_factory=Confidence)
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


# ---------------------------------------------------------------------------
# Attention — returned by AttentionSystem (AutonomyLoop)
# ---------------------------------------------------------------------------

@dataclass
class Attention:
    signal_id: str
    action: ActionDecision
    priority: Priority
    confidence: float                       # 0.0 – 1.0
    reason: str
    suggested_response: str | None = None


# ---------------------------------------------------------------------------
# ProactiveAction — audit log of what the autonomy loop actually did
# ---------------------------------------------------------------------------

@dataclass
class ProactiveAction:
    signal_id: str
    action_type: str                        # "notify", "execute_tool", "execute_workflow"
    description: str
    confidence: float
    why: str
    data_used: list[str] = field(default_factory=list)
    result: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
