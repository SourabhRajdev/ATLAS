"""Unified data models — collapsed from 5 types into Event + supporting structs."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Unified Event — replaces ExecutionTrace / StepTrace / ActionRecord / Plan / PlanStep
# ---------------------------------------------------------------------------

class EventType:
    THOUGHT = "thought"          # streamed model text
    TOOL_CALL = "tool_call"      # model invoked a tool
    TOOL_RESULT = "tool_result"  # tool returned
    PROGRESS = "progress"        # status update for UI
    DONE = "done"                # final response
    ERROR = "error"              # recoverable or fatal error
    PERCEPT = "percept"          # world-state change observed
    APPROVAL = "approval"        # awaiting user approval
    APPROVED = "approved"
    DENIED = "denied"


@dataclass
class Event:
    """The single unit of execution observability. Streams everywhere."""
    type: str
    content: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=_uid)
    timestamp: float = field(default_factory=time.time)
    task_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "timestamp": self.timestamp,
            "content": self.content if isinstance(self.content, (str, int, float, bool, dict, list, type(None))) else str(self.content),
            "metadata": self.metadata,
            "task_id": self.task_id,
        }


# ---------------------------------------------------------------------------
# Approval tiers (still used by ToolDef + capability gate)
# ---------------------------------------------------------------------------

class Tier(IntEnum):
    AUTO = 1        # execute silently
    NOTIFY = 2      # execute, but tell user
    CONFIRM = 3     # ask before executing


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

class ToolDef(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    tier: Tier = Tier.AUTO
    destructive: bool = False
    # Capability hints for the gate
    reads: list[str] = Field(default_factory=list)   # ["fs:~/Projects/**"]
    writes: list[str] = Field(default_factory=list)
    network: bool = False

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# ---------------------------------------------------------------------------
# Backwards-compat shims — keep existing call sites alive during migration
# These are the minimum surface old code touches.
# ---------------------------------------------------------------------------

class ActionRecord(BaseModel):
    """Legacy shim. New code uses Event(type=TOOL_RESULT)."""
    id: str = Field(default_factory=_uid)
    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    tier: Tier = Tier.AUTO
    approved: bool = True
    error: str | None = None
    cost_usd: float = 0.0
    created_at: str = Field(default_factory=_now)


class Message(BaseModel):
    id: str = Field(default_factory=_uid)
    session_id: str
    role: str
    content: str = ""
    tool_data: dict[str, Any] | None = None
    created_at: str = Field(default_factory=_now)


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=_uid)
    type: str
    content: str
    source: str | None = None
    importance: float = 0.5
    confidence: float = 0.8
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    expires_at: str | None = None


# ---------------------------------------------------------------------------
# Task state — survives restarts via checkpoint
# ---------------------------------------------------------------------------

@dataclass
class Budget:
    max_ms: int = 30_000
    max_tool_calls: int = 8
    max_tokens: int = 8_000
    started_at: float = field(default_factory=time.time)
    tool_calls_used: int = 0
    tokens_used: int = 0

    @staticmethod
    def for_query(query: str) -> Budget:
        words = len(query.split())
        has_email = "@" in query
        if words <= 5 and not has_email:
            return Budget(max_tool_calls=3, max_ms=15_000)
        if words <= 10 and not has_email:
            return Budget(max_tool_calls=4, max_ms=20_000)
        if query.lower().startswith(("what ", "who ", "when ", "where ", "how much ")):
            return Budget(max_tool_calls=4, max_ms=20_000)
        return Budget()

    @property
    def exhausted(self) -> bool:
        if (time.time() - self.started_at) * 1000 > self.max_ms:
            return True
        if self.tool_calls_used >= self.max_tool_calls:
            return True
        if self.tokens_used >= self.max_tokens:
            return True
        return False

    def consume_tool_calls(self, n: int = 1) -> None:
        self.tool_calls_used += n


@dataclass
class TaskState:
    id: str = field(default_factory=_uid)
    goal: str = ""
    session_id: str = ""
    messages: list[dict] = field(default_factory=list)  # Gemini Content dicts
    observations: list[Event] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    last_checkpoint: float = field(default_factory=time.time)
    success: bool = True
    final_result: str = ""

    def to_display(self) -> str:
        """Human-friendly trace for CLI."""
        lines = [f"goal: {self.goal}"]
        for ev in self.observations:
            if ev.type == EventType.TOOL_CALL:
                name = ev.content.get("name", "?") if isinstance(ev.content, dict) else "?"
                lines.append(f"  → {name}")
            elif ev.type == EventType.TOOL_RESULT:
                ok = "ok" if not ev.metadata.get("error") else "err"
                lines.append(f"    {ok}")
            elif ev.type == EventType.ERROR:
                lines.append(f"  ✗ {ev.content}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Legacy aliases — gradually delete as call sites migrate
# ---------------------------------------------------------------------------

ExecutionTrace = TaskState  # alias for transitional code
StepTrace = Event           # alias
