"""Signal detection — identifies things that need attention."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from atlas.autonomy.models import Signal
from atlas.memory.store import MemoryStore
from atlas.scheduler.scheduler import Scheduler

logger = logging.getLogger("atlas.signals")


class SignalDetector:
    """Detects signals from various sources."""
    
    def __init__(self, memory: MemoryStore, scheduler: Scheduler) -> None:
        self.memory = memory
        self.scheduler = scheduler

    async def detect_all(self) -> list[Signal]:
        """Run all signal detectors and return detected signals."""
        signals = []
        
        # Check scheduled tasks
        signals.extend(await self._detect_scheduled_tasks())
        
        # Check memory patterns
        signals.extend(await self._detect_memory_patterns())
        
        # Check anomalies
        signals.extend(await self._detect_anomalies())
        
        # Check for automation suggestions
        signals.extend(await self._detect_automation_opportunities())
        
        return signals

    async def _detect_scheduled_tasks(self) -> list[Signal]:
        """Check for due scheduled tasks."""
        signals = []
        
        try:
            due_tasks = self.scheduler.get_due_tasks()
            
            for task in due_tasks:
                signal = Signal(
                    type="scheduled_task",
                    source="scheduler",
                    description=f"Task '{task.name}' is due",
                    data={
                        "task_id": task.id,
                        "task_name": task.name,
                        "task_type": task.task_type,
                        "target": task.target,
                        "params": task.params,
                    }
                )
                signals.append(signal)
                
        except Exception as e:
            logger.error("Error detecting scheduled tasks: %s", e)
        
        return signals

    async def _detect_memory_patterns(self) -> list[Signal]:
        """Detect patterns in memory that might need attention."""
        signals = []
        
        try:
            # Check for repeated searches (user looking for same thing)
            recent_actions = self.memory.get_recent_actions(limit=20)
            
            # Count search patterns
            search_counts: dict[str, int] = {}
            for action in recent_actions:
                if action["tool_name"] == "web_search":
                    # Would need to parse params, simplified here
                    search_counts["search"] = search_counts.get("search", 0) + 1
            
            # If user searched 3+ times recently
            if search_counts.get("search", 0) >= 3:
                signal = Signal(
                    type="memory_pattern",
                    source="memory_analyzer",
                    description="You've searched multiple times recently — want me to summarize findings?",
                    data={"search_count": search_counts["search"]}
                )
                signals.append(signal)
                
        except Exception as e:
            logger.error("Error detecting memory patterns: %s", e)
        
        return signals

    async def _detect_anomalies(self) -> list[Signal]:
        """Detect anomalies in system behavior."""
        signals = []
        
        try:
            # Check for repeated failures
            recent_actions = self.memory.get_recent_actions(limit=15)
            
            failures = [a for a in recent_actions if not a["approved"] or a.get("error")]
            
            if len(failures) >= 3:
                signal = Signal(
                    type="anomaly",
                    source="anomaly_detector",
                    description=f"Detected {len(failures)} failed actions recently",
                    data={"failure_count": len(failures)}
                )
                signals.append(signal)
                
        except Exception as e:
            logger.error("Error detecting anomalies: %s", e)
        
        return signals

    async def _detect_automation_opportunities(self) -> list[Signal]:
        """Detect recurring actions that could be automated."""
        signals = []
        
        try:
            # Check for repeated tool usage patterns
            recent_actions = self.memory.get_recent_actions(limit=30)
            
            # Count tool usage
            tool_counts: dict[str, int] = {}
            for action in recent_actions:
                tool = action["tool_name"]
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            
            # If a tool is used 5+ times
            for tool, count in tool_counts.items():
                if count >= 5:
                    signal = Signal(
                        type="suggestion",
                        source="automation_detector",
                        description=f"You use '{tool}' frequently ({count} times) — want to automate it?",
                        data={"tool": tool, "count": count}
                    )
                    signals.append(signal)
                    
        except Exception as e:
            logger.error("Error detecting automation opportunities: %s", e)
        
        return signals
