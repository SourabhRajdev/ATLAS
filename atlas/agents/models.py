"""Multi-agent coordination models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class AgentRole(str, Enum):
    ORCHESTRATOR  = "orchestrator"
    RESEARCHER    = "researcher"
    EXECUTOR      = "executor"
    COMMUNICATOR  = "communicator"
    ANALYST       = "analyst"
    GUARDIAN      = "guardian"


class TaskStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    DONE       = "done"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class MessageKind(str, Enum):
    TASK_ASSIGN  = "task_assign"
    TASK_RESULT  = "task_result"
    STATUS_REQ   = "status_req"
    STATUS_RESP  = "status_resp"
    VETO         = "veto"
    ESCALATE     = "escalate"
    BROADCAST    = "broadcast"


@dataclass
class AgentMessage:
    id: str
    from_agent: str         # AgentRole value
    to_agent: str           # AgentRole value or "broadcast"
    kind: MessageKind
    payload: dict
    sent_at: float = field(default_factory=time.time)
    reply_to: Optional[str] = None   # message id this is replying to

    @classmethod
    def create(
        cls,
        from_agent: AgentRole | str,
        to_agent: AgentRole | str,
        kind: MessageKind,
        payload: dict,
        reply_to: str | None = None,
    ) -> "AgentMessage":
        return cls(
            id=str(uuid.uuid4()),
            from_agent=from_agent.value if isinstance(from_agent, AgentRole) else from_agent,
            to_agent=to_agent.value if isinstance(to_agent, AgentRole) else to_agent,
            kind=kind,
            payload=payload,
            reply_to=reply_to,
        )


@dataclass
class AgentTask:
    id: str
    assigned_to: str         # AgentRole value
    description: str
    context: dict
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    parent_task_id: Optional[str] = None

    @classmethod
    def create(
        cls,
        assigned_to: AgentRole | str,
        description: str,
        context: dict | None = None,
        parent_task_id: str | None = None,
    ) -> "AgentTask":
        return cls(
            id=str(uuid.uuid4()),
            assigned_to=assigned_to.value if isinstance(assigned_to, AgentRole) else assigned_to,
            description=description,
            context=context or {},
            parent_task_id=parent_task_id,
        )

    def start(self) -> None:
        self.status = TaskStatus.RUNNING
        self.started_at = time.time()

    def complete(self, result: Any) -> None:
        self.status = TaskStatus.DONE
        self.result = result
        self.completed_at = time.time()

    def fail(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = time.time()

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return None
