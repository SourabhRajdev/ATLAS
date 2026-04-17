"""Context gating — decides when to suppress actions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("atlas.gating")


class ContextGate:
    """Gates proactive actions based on user context."""
    
    def __init__(self) -> None:
        self.last_user_activity: datetime | None = None
        self.idle_threshold_seconds = 60  # Consider idle after 60s

    def update_activity(self) -> None:
        """Mark user as active."""
        self.last_user_activity = datetime.now(timezone.utc)

    def is_user_active(self) -> bool:
        """Check if user is currently active."""
        if not self.last_user_activity:
            return False
        
        idle_time = datetime.now(timezone.utc) - self.last_user_activity
        return idle_time.total_seconds() < self.idle_threshold_seconds

    def should_suppress(self, signal_type: str, priority: str) -> bool:
        """Decide if signal should be suppressed based on context."""
        
        user_active = self.is_user_active()
        
        # Never suppress high priority
        if priority == "high":
            return False
        
        # If user is active, suppress non-critical signals
        if user_active:
            # Allow scheduled tasks and anomalies
            if signal_type in ("scheduled_task", "anomaly"):
                return False
            
            # Suppress suggestions and patterns when user is active
            if signal_type in ("suggestion", "memory_pattern", "automation_opportunity"):
                logger.debug("Suppressing %s (user active)", signal_type)
                return True
        
        return False

    def get_context_info(self) -> dict:
        """Get current context information."""
        return {
            "user_active": self.is_user_active(),
            "last_activity": self.last_user_activity.isoformat() if self.last_user_activity else None,
        }
