"""Memory tools — let the LLM read/write long-term memories."""

from __future__ import annotations

from atlas.core.models import MemoryEntry, Tier
from atlas.memory.store import MemoryStore
from atlas.tools.registry import ToolRegistry


def register(registry: ToolRegistry, memory: MemoryStore) -> None:
    """Register memory tools that give the LLM direct access to the memory store."""

    @registry.register(
        name="save_memory",
        description=(
            "Save an important fact, preference, or decision to long-term memory. "
            "Use this when the user shares something worth remembering across sessions: "
            "preferences, project context, personal info, decisions made."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "What to remember"},
                "type": {
                    "type": "string",
                    "description": "Category",
                    "enum": ["fact", "preference", "decision", "contact", "note"],
                },
            },
            "required": ["content", "type"],
        },
        tier=Tier.NOTIFY,
    )
    def save_memory(content: str, type: str) -> str:
        entry = MemoryEntry(type=type, content=content)
        memory.add_memory(entry)
        return f"Saved to memory: [{type}] {content}"

    @registry.register(
        name="search_memory",
        description="Search long-term memory for previously stored information.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
            },
            "required": ["query"],
        },
        tier=Tier.AUTO,
    )
    def search_memory(query: str) -> str:
        results = memory.search_memories(query, limit=10)
        if not results:
            return "No matching memories found."
        lines = []
        for r in results:
            lines.append(f"[{r['type']}] {r['content']} (confidence: {r['confidence']})")
        return "\n".join(lines)
