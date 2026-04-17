"""Structured logging for autonomy system."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("atlas.autonomy.logger")


class AutonomyLogger:
    """Structured logger for autonomy decisions."""
    
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Keep last N decisions in memory for inspection
        self.recent_decisions: list[dict] = []
        self.max_recent = 50

    def log_cycle(
        self,
        cycle_num: int,
        detected_signals: list[dict],
        filtered_signals: list[dict],
        selected_signal: dict | None,
        decision: dict | None,
        execution_result: str | None = None,
    ) -> None:
        """Log a complete autonomy cycle."""
        
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle_num,
            "detected_count": len(detected_signals),
            "filtered_count": len(filtered_signals),
            "selected": selected_signal,
            "decision": decision,
            "execution_result": execution_result,
        }
        
        # Write to file
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        
        # Keep in memory
        self.recent_decisions.append(entry)
        if len(self.recent_decisions) > self.max_recent:
            self.recent_decisions.pop(0)
        
        logger.debug("Logged cycle %d: %d detected, %d filtered", 
                    cycle_num, len(detected_signals), len(filtered_signals))

    def get_recent_decisions(self, limit: int = 10) -> list[dict]:
        """Get recent autonomy decisions."""
        return self.recent_decisions[-limit:]

    def get_statistics(self) -> dict:
        """Get autonomy statistics."""
        if not self.recent_decisions:
            return {
                "total_cycles": 0,
                "signals_detected": 0,
                "signals_filtered": 0,
                "actions_taken": 0,
                "ignored_count": 0,
            }
        
        total_cycles = len(self.recent_decisions)
        signals_detected = sum(d["detected_count"] for d in self.recent_decisions)
        signals_filtered = sum(d["filtered_count"] for d in self.recent_decisions)
        
        actions_taken = sum(
            1 for d in self.recent_decisions 
            if d.get("decision", {}).get("action") in ("notify", "act")
        )
        
        ignored_count = sum(
            1 for d in self.recent_decisions
            if d.get("decision", {}).get("action") == "ignore" or not d.get("selected")
        )
        
        return {
            "total_cycles": total_cycles,
            "signals_detected": signals_detected,
            "signals_filtered": signals_filtered,
            "actions_taken": actions_taken,
            "ignored_count": ignored_count,
            "action_rate": actions_taken / total_cycles if total_cycles > 0 else 0,
        }

    def get_signal_types(self) -> dict[str, int]:
        """Get count of signal types detected."""
        type_counts: dict[str, int] = {}
        
        for decision in self.recent_decisions:
            selected = decision.get("selected")
            if selected:
                signal_type = selected.get("type", "unknown")
                type_counts[signal_type] = type_counts.get(signal_type, 0) + 1
        
        return type_counts
