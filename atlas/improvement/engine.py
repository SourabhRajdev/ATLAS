"""SelfImprovementEngine — high-level API for the improvement loop."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from atlas.improvement.analyzer import BehaviorAnalyzer
from atlas.improvement.models import ImpactLevel, QualitySignal, SignalKind, WeeklyReport
from atlas.improvement.monitor import BehaviorMonitor

logger = logging.getLogger("atlas.improvement.engine")


class SelfImprovementEngine:
    def __init__(self, data_dir: Path) -> None:
        self._monitor = BehaviorMonitor(data_dir / "improvement.db")
        self._analyzer = BehaviorAnalyzer(self._monitor)

    # ── Signal ingestion ──────────────────────────────────────────────────

    def on_task_timing(self, task_name: str, estimated_min: int, actual_min: int) -> None:
        self._monitor.record_task_timing(task_name, estimated_min, actual_min)

    def on_tool_error(self, tool_name: str, error: str) -> None:
        self._monitor.record_tool_error(tool_name, error)

    def on_user_message(self, message: str, context: str = "") -> None:
        self._monitor.record_user_feedback(message, context)

    def on_user_correction(self, context: str, detail: str = "") -> None:
        self._monitor.record_user_correction(context, detail)

    def on_goal_event(self, goal_title: str, completed: bool) -> None:
        self._monitor.record_goal_event(goal_title, completed)

    def record(self, signal: QualitySignal) -> None:
        self._monitor.record(signal)

    # ── Analysis ──────────────────────────────────────────────────────────

    def generate_report(self) -> WeeklyReport:
        return self._analyzer.generate_weekly_report()

    def get_recent_signals(self, days: int = 7) -> list[QualitySignal]:
        return self._monitor.get_recent_signals(days=days)

    def get_signal_counts(self, days: int = 7) -> dict[str, int]:
        return self._monitor.get_signal_counts(days=days)

    # ── Health ────────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        try:
            counts = self._monitor.get_signal_counts(days=7)
            total = sum(counts.values())
            return {
                "status": "healthy",
                "signals_last_7d": total,
                "signal_breakdown": counts,
            }
        except Exception as e:
            return {"status": "down", "error": str(e)}

    def close(self) -> None:
        self._monitor.close()
