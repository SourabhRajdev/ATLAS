"""Signal scoring — determines what deserves attention."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from atlas.autonomy.models import Priority, Signal

logger = logging.getLogger("atlas.scoring")

# Scoring weights
RELEVANCE_WEIGHT = 0.4
URGENCY_WEIGHT = 0.4
CONFIDENCE_WEIGHT = 0.2

# Priority thresholds
PRIORITY_THRESHOLD = 0.5  # Below this = IGNORE
HIGH_PRIORITY_THRESHOLD = 0.75
MEDIUM_PRIORITY_THRESHOLD = 0.55


class SignalScorer:
    """Scores signals for relevance, urgency, and confidence."""
    
    def __init__(self) -> None:
        self.cooldowns: dict[str, datetime] = {}  # signal_key -> last_shown
        
        # Cooldown periods by signal type (in hours)
        self.cooldown_periods = {
            "scheduled_task": 0,  # No cooldown for scheduled tasks
            "anomaly": 1,  # 1 hour
            "memory_pattern": 6,  # 6 hours
            "suggestion": 24,  # 24 hours
            "automation_opportunity": 24,  # 24 hours
        }

    def score_signal(self, signal: Signal, context: dict | None = None) -> dict:
        """Score a signal and return scoring breakdown."""
        
        # Check cooldown first
        if self._is_in_cooldown(signal):
            return {
                "relevance": 0.0,
                "urgency": 0.0,
                "confidence": 0.0,
                "final_priority": 0.0,
                "priority_level": Priority.LOW,
                "reason": "In cooldown period",
                "should_ignore": True,
            }
        
        # Calculate individual scores
        relevance = self._score_relevance(signal, context)
        urgency = self._score_urgency(signal)
        confidence = self._score_confidence(signal)
        
        # Weighted final priority
        final_priority = (
            relevance * RELEVANCE_WEIGHT +
            urgency * URGENCY_WEIGHT +
            confidence * CONFIDENCE_WEIGHT
        )
        
        # Determine priority level
        if final_priority >= HIGH_PRIORITY_THRESHOLD:
            priority_level = Priority.HIGH
        elif final_priority >= MEDIUM_PRIORITY_THRESHOLD:
            priority_level = Priority.MEDIUM
        else:
            priority_level = Priority.LOW
        
        # Should we ignore this signal?
        should_ignore = final_priority < PRIORITY_THRESHOLD
        
        return {
            "relevance": relevance,
            "urgency": urgency,
            "confidence": confidence,
            "final_priority": final_priority,
            "priority_level": priority_level,
            "should_ignore": should_ignore,
            "reason": self._explain_score(relevance, urgency, confidence, final_priority),
        }

    def _score_relevance(self, signal: Signal, context: dict | None) -> float:
        """Score how relevant this signal is to current context."""
        
        # Scheduled tasks are always relevant
        if signal.type == "scheduled_task":
            return 1.0
        
        # Anomalies are highly relevant
        if signal.type == "anomaly":
            return 0.9
        
        # Context-based relevance
        if context:
            user_active = context.get("user_active", False)
            current_focus = context.get("focus", "unknown")
            
            # If user is active, reduce relevance of suggestions
            if user_active and signal.type in ("suggestion", "memory_pattern"):
                return 0.3
            
            # If signal relates to current focus, boost relevance
            if signal.type == "suggestion":
                tool = signal.data.get("tool", "")
                if current_focus == "coding" and tool in ("read_file", "write_file"):
                    return 0.8
                if current_focus == "research" and tool in ("web_search", "fetch_url"):
                    return 0.8
        
        # Default relevance by type
        relevance_by_type = {
            "memory_pattern": 0.6,
            "suggestion": 0.5,
            "automation_opportunity": 0.7,
        }
        
        return relevance_by_type.get(signal.type, 0.5)

    def _score_urgency(self, signal: Signal) -> float:
        """Score how urgent this signal is."""
        
        # Scheduled tasks are urgent when due
        if signal.type == "scheduled_task":
            return 1.0
        
        # Anomalies are urgent
        if signal.type == "anomaly":
            failure_count = signal.data.get("failure_count", 0)
            return min(failure_count / 5, 1.0)  # Cap at 1.0
        
        # Suggestions are not urgent
        if signal.type in ("suggestion", "memory_pattern", "automation_opportunity"):
            return 0.2
        
        return 0.5

    def _score_confidence(self, signal: Signal) -> float:
        """Score confidence in this signal."""
        
        # Scheduled tasks have high confidence
        if signal.type == "scheduled_task":
            return 1.0
        
        # Anomalies have medium-high confidence
        if signal.type == "anomaly":
            return 0.8
        
        # Suggestions based on frequency
        if signal.type in ("suggestion", "automation_opportunity"):
            count = signal.data.get("count", 0)
            # Confidence increases with frequency
            if count >= 10:
                return 0.9
            elif count >= 7:
                return 0.8
            elif count >= 5:
                return 0.7
            else:
                return 0.5
        
        # Memory patterns have lower confidence
        if signal.type == "memory_pattern":
            return 0.6
        
        return 0.5

    def _is_in_cooldown(self, signal: Signal) -> bool:
        """Check if signal is in cooldown period."""
        
        cooldown_hours = self.cooldown_periods.get(signal.type, 0)
        if cooldown_hours == 0:
            return False
        
        # Create unique key for this signal
        signal_key = f"{signal.type}:{signal.source}:{signal.description[:50]}"
        
        if signal_key in self.cooldowns:
            last_shown = self.cooldowns[signal_key]
            cooldown_until = last_shown + timedelta(hours=cooldown_hours)
            
            if datetime.now(timezone.utc) < cooldown_until:
                logger.debug("Signal in cooldown: %s", signal_key)
                return True
        
        return False

    def mark_shown(self, signal: Signal) -> None:
        """Mark signal as shown (starts cooldown)."""
        signal_key = f"{signal.type}:{signal.source}:{signal.description[:50]}"
        self.cooldowns[signal_key] = datetime.now(timezone.utc)
        logger.debug("Signal cooldown started: %s", signal_key)

    def _explain_score(self, relevance: float, urgency: float, confidence: float, final: float) -> str:
        """Generate human-readable explanation of score."""
        parts = []
        
        if relevance >= 0.8:
            parts.append("highly relevant")
        elif relevance >= 0.6:
            parts.append("relevant")
        elif relevance < 0.4:
            parts.append("low relevance")
        
        if urgency >= 0.8:
            parts.append("urgent")
        elif urgency < 0.3:
            parts.append("not urgent")
        
        if confidence >= 0.8:
            parts.append("high confidence")
        elif confidence < 0.6:
            parts.append("uncertain")
        
        if not parts:
            return f"Priority: {final:.0%}"
        
        return f"{', '.join(parts)} (priority: {final:.0%})"
