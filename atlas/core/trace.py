"""Execution trace — logs plan execution for visibility and debugging."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StepTrace(BaseModel):
    """Trace of a single step execution."""
    step_index: int
    step_type: str  # tool | reason
    tool_name: str | None = None
    params: dict[str, Any] = {}
    result: Any = None
    error: str | None = None
    started_at: str = Field(default_factory=_now)
    completed_at: str | None = None
    duration_ms: int = 0


class ExecutionTrace(BaseModel):
    """Complete trace of a plan execution."""
    session_id: str
    goal: str
    plan_steps: int
    steps: list[StepTrace] = []
    started_at: str = Field(default_factory=_now)
    completed_at: str | None = None
    total_duration_ms: int = 0
    success: bool = True
    final_result: str = ""

    def add_step(self, trace: StepTrace) -> None:
        """Add a step trace."""
        self.steps.append(trace)

    def complete(self, result: str, success: bool = True) -> None:
        """Mark execution as complete."""
        self.completed_at = _now()
        self.final_result = result
        self.success = success
        
        # Calculate total duration
        if self.steps:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at)
            self.total_duration_ms = int((end - start).total_seconds() * 1000)

    def to_display(self) -> str:
        """Format trace for CLI display."""
        lines = [
            f"Goal: {self.goal}",
            f"Steps: {len(self.steps)}/{self.plan_steps}",
            "",
        ]

        for i, step in enumerate(self.steps, 1):
            status = "✓" if not step.error else "✗"
            if step.tool_name:
                lines.append(f"{status} Step {i}: {step.tool_name}({_format_params(step.params)})")
            else:
                lines.append(f"{status} Step {i}: {step.step_type}")
            
            if step.error:
                lines.append(f"  Error: {step.error}")
            elif step.result and isinstance(step.result, str):
                preview = step.result[:100] + "..." if len(step.result) > 100 else step.result
                lines.append(f"  → {preview}")

        lines.append("")
        lines.append(f"Duration: {self.total_duration_ms}ms | Success: {self.success}")
        
        return "\n".join(lines)


def _format_params(params: dict) -> str:
    """Format parameters for display."""
    if not params:
        return ""
    items = []
    for k, v in params.items():
        if isinstance(v, str) and len(v) > 30:
            v = v[:30] + "..."
        items.append(f"{k}={v}")
    return ", ".join(items)
