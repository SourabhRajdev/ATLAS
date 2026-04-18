"""WorldModelUpdater — processes WorldEvents into entity graph updates.

Consumes WorldEvents and calls WorldModel methods to upsert entities,
update attributes, and strengthen relationships.

Does NOT call any LLM. Does NOT handle scheduling. Does NOT handle storage.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from atlas.world.extractor import (
    ExtractedMention, extract_from_email, extract_from_git_commit, extract_from_text,
)
from atlas.world.models import Entity, EntityType, WorldEvent

if TYPE_CHECKING:
    from atlas.world.world_model import WorldModel

logger = logging.getLogger("atlas.world.updater")


class WorldModelUpdater:
    def __init__(self, world: "WorldModel") -> None:
        self._world = world

    async def process_event(self, event: WorldEvent) -> list[Entity]:
        """Process a WorldEvent → extract entities → upsert into graph."""
        handler = _EVENT_HANDLERS.get(event.event_type, _handle_generic)
        try:
            return await handler(event, self._world)
        except Exception as e:
            logger.error("WorldModelUpdater failed on %s: %s", event.event_type, e)
            return []


# ------------------------------------------------------------------
# Event-type handlers
# ------------------------------------------------------------------

async def _handle_email_received(event: WorldEvent, world: "WorldModel") -> list[Entity]:
    payload = event.payload
    sender = payload.get("sender", "")
    subject = payload.get("subject", "")
    body = payload.get("body", "")[:2000]  # cap body processing

    mentions = extract_from_email(sender, subject, body)
    entities = []
    person_ids = []

    for mention in mentions:
        entity = await world.upsert_entity(
            type=mention.type,
            name=mention.name,
            source="gmail",
            metadata={"context": mention.context},
        )
        entities.append(entity)

        # Store email as attribute on Person entities
        if mention.type == EntityType.PERSON and "@" in mention.name:
            await world.update_attribute(entity.id, "email", mention.name, "gmail")
        elif mention.type == EntityType.PERSON:
            person_ids.append(entity.id)

    # If multiple people mentioned → they know each other (weak signal)
    if len(person_ids) >= 2:
        await world.link_entities(
            person_ids[0], person_ids[1], "works_with", strength=0.3, source="gmail"
        )

    return entities


async def _handle_imessage_received(event: WorldEvent, world: "WorldModel") -> list[Entity]:
    payload = event.payload
    sender = payload.get("sender", "")
    text = payload.get("text", "")[:1000]

    mentions = extract_from_text(f"{sender} {text}", source="imessage")
    entities = []
    for mention in mentions:
        entity = await world.upsert_entity(
            type=mention.type,
            name=mention.name,
            source="imessage",
            metadata={"context": mention.context},
        )
        entities.append(entity)
    return entities


async def _handle_git_commit(event: WorldEvent, world: "WorldModel") -> list[Entity]:
    payload = event.payload
    message = payload.get("message", "")
    author = payload.get("author", "")
    repo = payload.get("repo", "")

    mentions = extract_from_git_commit(message, author)
    entities = []

    # Repo itself is always a Project entity
    if repo:
        repo_entity = await world.upsert_entity(
            type=EntityType.PROJECT,
            name=repo,
            source="git",
            metadata={"type": "repository"},
        )
        entities.append(repo_entity)

    for mention in mentions:
        entity = await world.upsert_entity(
            type=mention.type,
            name=mention.name,
            source="git",
            metadata={"context": mention.context},
        )
        entities.append(entity)

        # Link person to repo
        if mention.type == EntityType.PERSON and repo:
            repo_entity_id = entities[0].id if entities else None
            if repo_entity_id:
                await world.link_entities(
                    entity.id, repo_entity_id, "works_on", strength=0.6, source="git"
                )

    return entities


async def _handle_calendar_event(event: WorldEvent, world: "WorldModel") -> list[Entity]:
    payload = event.payload
    title = payload.get("title", "")
    attendees = payload.get("attendees", [])

    mentions = extract_from_text(title, source="calendar")
    entities = []

    for mention in mentions:
        entity = await world.upsert_entity(
            type=mention.type,
            name=mention.name,
            source="calendar",
        )
        entities.append(entity)

    # Attendees are high-confidence Person entities
    for attendee in attendees:
        if isinstance(attendee, str) and attendee:
            entity = await world.upsert_entity(
                type=EntityType.PERSON,
                name=attendee,
                source="calendar",
                metadata={"event": title},
            )
            entities.append(entity)

    # Mutual attendees → works_with relationship
    attendee_entities = [e for e in entities if e.type == EntityType.PERSON]
    for i in range(len(attendee_entities)):
        for j in range(i + 1, len(attendee_entities)):
            await world.link_entities(
                attendee_entities[i].id, attendee_entities[j].id,
                "works_with", strength=0.4, source="calendar",
            )

    return entities


async def _handle_file_saved(event: WorldEvent, world: "WorldModel") -> list[Entity]:
    payload = event.payload
    path = payload.get("path", "")
    if not path:
        return []

    # Extract project from file path
    from pathlib import Path as _Path
    parts = _Path(path).parts
    # Look for src/, lib/, atlas/ style project dirs
    for part in parts:
        if part not in {".", "..", "/", "Users", "home"} and len(part) > 2:
            entity = await world.upsert_entity(
                type=EntityType.PROJECT,
                name=part,
                source="file_system",
                metadata={"path": path},
            )
            return [entity]
    return []


async def _handle_generic(event: WorldEvent, world: "WorldModel") -> list[Entity]:
    """Fallback: try to extract entities from any string values in payload."""
    text = " ".join(str(v) for v in event.payload.values() if isinstance(v, str))
    if not text.strip():
        return []
    mentions = extract_from_text(text[:500], source=event.source)
    entities = []
    for mention in mentions:
        entity = await world.upsert_entity(
            type=mention.type,
            name=mention.name,
            source=event.source,
        )
        entities.append(entity)
    return entities


_EVENT_HANDLERS = {
    "email_received": _handle_email_received,
    "imessage_received": _handle_imessage_received,
    "git_commit": _handle_git_commit,
    "calendar_event": _handle_calendar_event,
    "file_saved": _handle_file_saved,
}
