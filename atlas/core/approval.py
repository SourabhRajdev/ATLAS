"""Action approval — decides what runs automatically vs. needs human confirmation."""

from __future__ import annotations

from atlas.core.models import Tier, ToolDef


def needs_confirmation(tool: ToolDef, params: dict) -> bool:
    """Return True if this action requires user approval before executing."""
    if tool.tier == Tier.CONFIRM:
        return True
    if tool.tier == Tier.AUTO:
        return False
    # NOTIFY tier: auto-execute but we log it
    return False


def describe_action(tool: ToolDef, params: dict) -> str:
    """Human-readable description of what we're about to do."""
    name = tool.name
    if name == "run_shell":
        return f"Run shell command: `{params.get('command', '?')}`"
    if name == "write_file":
        return f"Write to file: {params.get('path', '?')}"
    if name == "delete_file":
        return f"Delete: {params.get('path', '?')}"
    if name == "send_email":
        return f"Send email to: {params.get('to', '?')}"
    # Generic fallback
    param_str = ", ".join(f"{k}={_truncate(str(v))}" for k, v in params.items())
    return f"{name}({param_str})"


def _truncate(s: str, n: int = 60) -> str:
    return s[:n] + "..." if len(s) > n else s
