"""Data models for continuity system."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ThreadType(str, Enum):
    """Type of ongoing thread."""
    TASK = "task"
    RESEARCH = "research"
    REMINDER = "reminder"
    CONVERSATION = "conversation"
    MONITORING = "monitoring"


class ThreadState(str, Enum):
    """State of a thread."""
    ACTIVE = "active"
    PENDING = "pending"
    COMPLETED = "completed"
    PAUSED = "paused"


class Thread(BaseModel):
    """Represents an ongoing context thread."""
    
    thread_id: str
    type: ThreadType
    state: ThreadState
    
    # Content
    title: str
    description: str
    context: dict[str, Any] = Field(default_factory=dict)
    
    # Temporal
    created_at: str
    last_update: str
    last_surfaced: str | None = None
    
    # Priority
    priority: float = 0.5  # 0.0-1.0
    
    # Metadata
    session_id: str | None = None
    related_memories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    
    def is_stale(self, hours: int = 24) -> bool:
        """Check if thread is stale (not updated recently)."""
        from datetime import timedelta
        
        last_update_dt = datetime.fromisoformat(self.last_update)
        now = datetime.now(timezone.utc)
        
        return (now - last_update_dt) > timedelta(hours=hours)
    
    def should_surface(self, cooldown_hours: int = 6) -> bool:
        """Check if thread should be surfaced to user."""
        if self.state != ThreadState.ACTIVE:
            return False
        
        if self.priority < 0.5:
            return False
        
        # Check cooldown
        if self.last_surfaced:
            from datetime import timedelta
            
            last_surfaced_dt = datetime.fromisoformat(self.last_surfaced)
            now = datetime.now(timezone.utc)
            
            if (now - last_surfaced_dt) < timedelta(hours=cooldown_hours):
                return False
        
        return True
    
    def get_age_hours(self) -> float:
        """Get age of thread in hours."""
        created_dt = datetime.fromisoformat(self.created_at)
        now = datetime.now(timezone.utc)
        
        delta = now - created_dt
        return delta.total_seconds() / 3600


class ThreadUpdate(BaseModel):
    """Update to a thread."""
    
    thread_id: str
    update_type: str  # "progress", "completion", "note", "priority"
    content: str
    timestamp: str
    metadata: dict[str, Any] = Field(default_factory=dict)
