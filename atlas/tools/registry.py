"""Tool registry — register functions, expose to Anthropic API, execute with logging."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

from atlas.core.models import ActionRecord, Tier, ToolDef

logger = logging.getLogger("atlas.tools")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}
        self._handlers: dict[str, Callable] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        tier: Tier = Tier.AUTO,
        destructive: bool = False,
    ) -> Callable:
        """Decorator to register a tool function."""
        def decorator(fn: Callable) -> Callable:
            self._tools[name] = ToolDef(
                name=name,
                description=description,
                parameters=parameters,
                tier=tier,
                destructive=destructive,
            )
            self._handlers[name] = fn
            logger.info("Registered tool: %s (tier=%s)", name, tier.name)
            return fn
        return decorator

    def get_anthropic_tools(self) -> list[dict]:
        """Return tool definitions in Anthropic API format."""
        return [t.to_anthropic() for t in self._tools.values()]

    def get_tool(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    async def execute(self, name: str, params: dict[str, Any]) -> ActionRecord:
        """Execute a tool and return an ActionRecord."""
        tool = self._tools.get(name)
        if not tool:
            return ActionRecord(
                tool_name=name, params=params,
                error=f"Unknown tool: {name}", approved=False,
            )

        record = ActionRecord(
            tool_name=name, params=params, tier=tool.tier,
        )

        try:
            handler = self._handlers[name]
            if inspect.iscoroutinefunction(handler):
                result = await handler(**params)
            else:
                result = await asyncio.to_thread(handler, **params)
            record.result = result
            logger.info("Tool %s executed: %s", name, _truncate(str(result)))
        except Exception as e:
            record.error = f"{type(e).__name__}: {e}"
            record.result = None
            logger.error("Tool %s failed: %s", name, record.error)

        return record


def _truncate(s: str, max_len: int = 200) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s
