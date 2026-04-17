"""Integration layer — Connects continuity with engine and autonomy."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from atlas.continuity.models import Thread, ThreadState, ThreadType, ThreadUpdate
from atlas.continuity.threads import ThreadManager

if TYPE_CHECKING:
    from atlas.memory.store import MemoryStore

logger = logging.getLogger("atlas.continuity.integration")


class ContinuityIntegration:
    """Integrates continuity system with engine and autonomy."""
    
    def __init__(
        self,
        thread_manager: ThreadManager,
        memory: MemoryStore,
    ) -> None:
        self.threads = thread_manager
        self.memory = memory
    
    def check_context_continuity(
        self,
        user_query: str,
        session_id: str,
    ) -> tuple[Thread | None, str]:
        """Check if query relates to an active thread.
        
        Returns:
            (related_thread, context_hint)
        """
        # Find related thread
        thread = self.threads.find_related_thread(user_query, session_id)
        
        if not thread:
            return None, ""
        
        # Generate context hint (minimal, natural)
        hint = self._generate_context_hint(thread, user_query)
        
        return thread, hint
    
    def _generate_context_hint(self, thread: Thread, query: str) -> str:
        """Generate a minimal context hint for the engine."""
        # Get age
        age_hours = thread.get_age_hours()
        
        # Temporal reference
        if age_hours < 1:
            time_ref = "just now"
        elif age_hours < 6:
            time_ref = "earlier"
        elif age_hours < 24:
            time_ref = "earlier today"
        elif age_hours < 48:
            time_ref = "yesterday"
        else:
            days = int(age_hours / 24)
            time_ref = f"{days} days ago"
        
        # Build hint
        hint = f"[Context: User asked about '{thread.title}' {time_ref}. Thread: {thread.thread_id}]"
        
        return hint
    
    def create_thread_from_query(
        self,
        user_query: str,
        session_id: str,
        type: ThreadType = ThreadType.CONVERSATION,
    ) -> Thread:
        """Create a thread from a user query."""
        # Extract title (first 50 chars)
        title = user_query[:50]
        if len(user_query) > 50:
            title += "..."
        
        thread = self.threads.create_thread(
            type=type,
            title=title,
            description=user_query,
            context={"original_query": user_query},
            priority=0.5,
            session_id=session_id,
        )
        
        return thread
    
    def update_thread_from_response(
        self,
        thread_id: str,
        response: str,
        success: bool = True,
    ) -> None:
        """Update thread with response."""
        now = datetime.now(timezone.utc).isoformat()
        
        update = ThreadUpdate(
            thread_id=thread_id,
            update_type="progress" if success else "error",
            content=response[:200],  # Store summary
            timestamp=now,
            metadata={"success": success},
        )
        
        self.threads.add_update(update)
    
    def surface_thread_if_needed(
        self,
        thread: Thread,
        cooldown_hours: int = 6,
    ) -> str | None:
        """Surface thread to user if appropriate.
        
        Returns:
            Notification message or None
        """
        if not thread.should_surface(cooldown_hours):
            return None
        
        # Mark as surfaced
        self.threads.mark_surfaced(thread.thread_id)
        
        # Generate minimal notification
        message = self._generate_surface_message(thread)
        
        return message
    
    def _generate_surface_message(self, thread: Thread) -> str:
        """Generate a minimal surface message."""
        # Get age
        age_hours = thread.get_age_hours()
        
        # Temporal reference
        if age_hours < 6:
            time_ref = "earlier"
        elif age_hours < 24:
            time_ref = "earlier today"
        elif age_hours < 48:
            time_ref = "yesterday"
        else:
            days = int(age_hours / 24)
            time_ref = f"{days} days ago"
        
        # Build message (minimal)
        if thread.type == ThreadType.TASK:
            message = f"task from {time_ref} — {thread.title}"
        elif thread.type == ThreadType.RESEARCH:
            message = f"research from {time_ref} — {thread.title}"
        elif thread.type == ThreadType.REMINDER:
            message = f"reminder: {thread.title}"
        else:
            message = f"from {time_ref} — {thread.title}"
        
        return message
    
    def get_continuity_context(self, session_id: str) -> str:
        """Get continuity context for engine prompt."""
        # Get active threads for this session
        all_threads = self.threads.get_active_threads(limit=10)
        session_threads = [t for t in all_threads if t.session_id == session_id]
        
        if not session_threads:
            return ""
        
        # Build context (minimal)
        lines = ["Active threads:"]
        for thread in session_threads[:3]:  # Max 3
            age_hours = thread.get_age_hours()
            if age_hours < 24:
                lines.append(f"- {thread.title} (ongoing)")
        
        return "\n".join(lines) if len(lines) > 1 else ""
    
    def detect_thread_completion(
        self,
        thread: Thread,
        response: str,
    ) -> bool:
        """Detect if thread should be marked as completed."""
        # Simple heuristics
        completion_markers = [
            "done",
            "completed",
            "finished",
            "resolved",
            "closed",
        ]
        
        response_lower = response.lower()
        
        return any(marker in response_lower for marker in completion_markers)
    
    def auto_complete_thread_if_done(
        self,
        thread_id: str,
        response: str,
    ) -> None:
        """Auto-complete thread if response indicates completion."""
        thread = self.threads.get_thread(thread_id)
        if not thread:
            return
        
        if self.detect_thread_completion(thread, response):
            self.threads.complete_thread(thread_id)
            logger.info("Auto-completed thread: %s", thread.title)
