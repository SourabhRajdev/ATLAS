"""ContextAssembler — builds the [WORLD CONTEXT] block injected into LLM prompts.

Given a query and a token budget, selects the most relevant entities and
formats them into a compact string. NEVER exceeds the token budget.

Does NOT call any LLM. Does NOT modify world state. Pure read + format.
"""

from __future__ import annotations

import time
import logging
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from atlas.world.models import EntityType, WorldContext

if TYPE_CHECKING:
    from atlas.world.world_model import WorldModel

logger = logging.getLogger("atlas.world.assembler")

# Rough token estimate: 4 chars ≈ 1 token
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


class ContextAssembler:
    def __init__(self, world: "WorldModel") -> None:
        self._world = world

    async def assemble(self, query: str, token_budget: int = 2000) -> WorldContext:
        """Build world context string within token_budget."""
        import asyncio

        # Phase 1: find entities directly mentioned in the query
        query_entities = await self._world.search_entities(query, limit=5)

        # Phase 2: recent high-confidence entities (active projects, people)
        recent_people = await asyncio.to_thread(
            self._world._conn.execute,
            """SELECT * FROM entities WHERE type = ? AND confidence > 0.5
               ORDER BY last_updated DESC LIMIT 5""",
            (EntityType.PERSON,),
        )
        recent_projects = await asyncio.to_thread(
            self._world._conn.execute,
            """SELECT * FROM entities WHERE type = ? AND confidence > 0.7
               AND last_updated > ?
               ORDER BY last_updated DESC LIMIT 5""",
            (EntityType.PROJECT, time.time() - 7 * 86_400),
        )
        commitments = await asyncio.to_thread(
            self._world._conn.execute,
            """SELECT * FROM entities WHERE type = ? AND confidence > 0.4
               ORDER BY last_updated DESC LIMIT 5""",
            (EntityType.COMMITMENT,),
        )

        from atlas.world.world_model import _row_to_entity
        all_people = [_row_to_entity(r) for r in recent_people.fetchall()]
        all_projects = [_row_to_entity(r) for r in recent_projects.fetchall()]
        all_commitments = [_row_to_entity(r) for r in commitments.fetchall()]

        # Merge query_entities into the priority pools
        query_entity_ids = {e.id for e in query_entities}
        for e in query_entities:
            if e.type == EntityType.PERSON and e not in all_people:
                all_people.insert(0, e)
            elif e.type == EntityType.PROJECT and e not in all_projects:
                all_projects.insert(0, e)

        # Build sections within budget
        lines: list[str] = ["[WORLD CONTEXT]"]
        chars_used = len("[WORLD CONTEXT]\n[END WORLD CONTEXT]")
        budget_chars = token_budget * _CHARS_PER_TOKEN
        truncated = False
        included_ids: list[str] = []

        def remaining() -> int:
            return budget_chars - chars_used - 10  # 10-char margin

        def add_line(line: str) -> bool:
            nonlocal chars_used
            needed = len(line) + 1  # +1 for \n
            if needed > remaining():
                return False
            lines.append(line)
            chars_used += needed
            return True

        # People section
        now = time.time()
        if all_people:
            add_line("People:")
            for entity in all_people[:5]:
                attrs = self._world.get_attributes(entity.id)
                attr_str = ", ".join(
                    f"{a.key}: {a.value[:30]}"
                    for a in attrs[:3]
                    if a.is_current()
                )
                days_ago = int((now - entity.last_updated) / 86_400)
                contact_str = f"{days_ago}d ago" if days_ago > 0 else "today"
                line = (
                    f"  {entity.name}"
                    f"{f' — {attr_str}' if attr_str else ''}"
                    f", last contact {contact_str}"
                )
                if not add_line(line):
                    truncated = True
                    break
                included_ids.append(entity.id)

        # Projects section
        if all_projects and remaining() > 100:
            add_line("Projects:")
            for entity in all_projects[:5]:
                attrs = {a.key: a.value for a in self._world.get_attributes(entity.id) if a.is_current()}
                status = attrs.get("status", "active")
                completion = attrs.get("completion_pct", "?")
                next_task = attrs.get("next_task", "")
                line = (
                    f"  {entity.name} — {status}"
                    f"{f', {completion}% done' if completion != '?' else ''}"
                    f"{f', next: {next_task[:40]}' if next_task else ''}"
                )
                if not add_line(line):
                    truncated = True
                    break
                included_ids.append(entity.id)

        # Commitments section
        if all_commitments and remaining() > 80:
            add_line("Commitments:")
            for entity in all_commitments[:3]:
                attrs = {a.key: a.value for a in self._world.get_attributes(entity.id) if a.is_current()}
                person = attrs.get("to_person", "someone")
                thing = attrs.get("what", entity.name[:50])
                due = attrs.get("due_date", "")
                line = f"  You owe {person}: {thing}{f' by {due}' if due else ''}"
                if not add_line(line):
                    truncated = True
                    break
                included_ids.append(entity.id)

        lines.append("[END WORLD CONTEXT]")
        text = "\n".join(lines)

        return WorldContext(
            text=text,
            token_estimate=_estimate_tokens(text),
            entities_included=included_ids,
            truncated=truncated,
        )
