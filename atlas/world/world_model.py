"""WorldModel — persistent knowledge graph of the user's life.

Stores typed entities (Person, Project, Commitment, etc.) with attributes,
relationships, and confidence scores. NOT a chat log. NOT a memory store.

Does NOT call any LLM. Does NOT use external APIs.
All intelligence is in the Updater and Extractor layers above this.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from atlas.world.models import (
    Attribute, Entity, EntityType, Relationship,
    WorldContext, WorldEvent, SOURCE_RELIABILITY, _canonicalize,
)
from atlas.world.schema import open_db

logger = logging.getLogger("atlas.world")

# Entity dedup: 85% canonical_name similarity = same entity
_DEDUP_THRESHOLD = 0.85


class WorldModel:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = open_db(db_path)
        logger.info("WorldModel initialized: %s", db_path)

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    async def upsert_entity(
        self,
        type: str,
        name: str,
        source: str,
        metadata: dict | None = None,
    ) -> Entity:
        return await asyncio.to_thread(
            self._upsert_entity_sync, type, name, source, metadata or {}
        )

    def _upsert_entity_sync(
        self, type: str, name: str, source: str, metadata: dict
    ) -> Entity:
        canonical = _canonicalize(name)
        now = time.time()
        source_confidence = SOURCE_RELIABILITY.get(source, 0.6)

        # Check for existing entity by canonical name similarity
        existing = self._find_similar_entity(canonical, type)
        if existing:
            # Reinforce existing entity — bump confidence and timestamps
            new_confidence = min(1.0, existing.confidence + 0.05)
            merged_meta = {**existing.metadata, **metadata}
            self._conn.execute(
                """UPDATE entities SET
                   confidence = ?, last_updated = ?, last_reinforced = ?,
                   metadata = ?
                   WHERE id = ?""",
                (new_confidence, now, now, json.dumps(merged_meta), existing.id),
            )
            self._conn.commit()
            existing.confidence = new_confidence
            existing.last_updated = now
            existing.last_reinforced = now
            existing.metadata = merged_meta
            return existing

        # Create new entity
        entity = Entity.new(type=type, name=name, source=source, metadata=metadata)
        entity.confidence = source_confidence
        self._conn.execute(
            """INSERT INTO entities
               (id, type, name, canonical_name, confidence,
                first_seen, last_updated, last_reinforced, source, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity.id, entity.type, entity.name, entity.canonical_name,
             entity.confidence, entity.first_seen, entity.last_updated,
             entity.last_reinforced, entity.source, json.dumps(entity.metadata)),
        )
        self._conn.commit()
        logger.debug("New entity: %s/%s", type, name)
        return entity

    def _find_similar_entity(self, canonical: str, type: str) -> Entity | None:
        rows = self._conn.execute(
            "SELECT * FROM entities WHERE type = ? ORDER BY confidence DESC LIMIT 50",
            (type,),
        ).fetchall()
        best_ratio = 0.0
        best: Entity | None = None
        for row in rows:
            ratio = SequenceMatcher(None, canonical, row["canonical_name"]).ratio()
            if ratio >= _DEDUP_THRESHOLD and ratio > best_ratio:
                best_ratio = ratio
                best = _row_to_entity(row)
        return best

    async def get_entity(self, name_or_id: str) -> Entity | None:
        return await asyncio.to_thread(self._get_entity_sync, name_or_id)

    def _get_entity_sync(self, name_or_id: str) -> Entity | None:
        # Try by ID first
        row = self._conn.execute(
            "SELECT * FROM entities WHERE id = ?", (name_or_id,)
        ).fetchone()
        if row:
            return _row_to_entity(row)
        # Try by canonical name
        canonical = _canonicalize(name_or_id)
        row = self._conn.execute(
            "SELECT * FROM entities WHERE canonical_name = ? ORDER BY confidence DESC LIMIT 1",
            (canonical,),
        ).fetchone()
        if row:
            return _row_to_entity(row)
        # Fuzzy match
        return self._find_similar_entity(canonical, type="")  # any type

    async def search_entities(
        self,
        query: str,
        type_filter: str | None = None,
        limit: int = 10,
    ) -> list[Entity]:
        return await asyncio.to_thread(
            self._search_entities_sync, query, type_filter, limit
        )

    def _search_entities_sync(
        self, query: str, type_filter: str | None, limit: int
    ) -> list[Entity]:
        canonical = _canonicalize(query)
        if type_filter:
            rows = self._conn.execute(
                """SELECT * FROM entities WHERE type = ?
                   ORDER BY confidence DESC, last_updated DESC LIMIT ?""",
                (type_filter, limit * 3),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM entities ORDER BY confidence DESC, last_updated DESC LIMIT ?",
                (limit * 3,),
            ).fetchall()

        scored = []
        for row in rows:
            ratio = SequenceMatcher(None, canonical, row["canonical_name"]).ratio()
            if ratio > 0.3 or canonical in row["canonical_name"] or row["canonical_name"] in canonical:
                scored.append((ratio, _row_to_entity(row)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:limit]]

    # ------------------------------------------------------------------
    # Attributes (with conflict resolution)
    # ------------------------------------------------------------------

    async def update_attribute(
        self,
        entity_id: str,
        key: str,
        value: str,
        source: str,
        confidence: float | None = None,
    ) -> Attribute:
        return await asyncio.to_thread(
            self._update_attribute_sync, entity_id, key, value, source, confidence
        )

    def _update_attribute_sync(
        self, entity_id: str, key: str, value: str, source: str, confidence: float | None
    ) -> Attribute:
        now = time.time()
        source_confidence = confidence or SOURCE_RELIABILITY.get(source, 0.6)

        # Find existing attribute for this entity+key+source
        existing = self._conn.execute(
            "SELECT * FROM attributes WHERE entity_id = ? AND key = ? AND source = ? "
            "AND superseded_by IS NULL",
            (entity_id, key, source),
        ).fetchone()

        if existing:
            # Same source updating same attribute — check if newer is more reliable
            if existing["value"] == value:
                # No change — just touch the entity timestamp
                self._conn.execute(
                    "UPDATE entities SET last_updated = ? WHERE id = ?",
                    (now, entity_id),
                )
                self._conn.commit()
                return _row_to_attribute(existing)

            # Value changed — supersede old entry, insert new
            new_id = self._insert_attribute(entity_id, key, value, source_confidence, source, now)
            self._conn.execute(
                "UPDATE attributes SET superseded_by = ? WHERE id = ?",
                (new_id, existing["id"]),
            )
        else:
            # New attribute for this source — check for conflicting values from other sources
            # Keep both, confidence-weighted by source reliability
            new_id = self._insert_attribute(entity_id, key, value, source_confidence, source, now)

        self._conn.execute(
            "UPDATE entities SET last_updated = ? WHERE id = ?", (now, entity_id)
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT * FROM attributes WHERE id = ?", (new_id,)
        ).fetchone()
        return _row_to_attribute(row)

    def _insert_attribute(
        self, entity_id: str, key: str, value: str,
        confidence: float, source: str, now: float
    ) -> int:
        cur = self._conn.execute(
            """INSERT OR REPLACE INTO attributes
               (entity_id, key, value, confidence, source, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entity_id, key, value, confidence, source, now),
        )
        return cur.lastrowid

    def get_attributes(self, entity_id: str) -> list[Attribute]:
        rows = self._conn.execute(
            "SELECT * FROM attributes WHERE entity_id = ? AND superseded_by IS NULL "
            "ORDER BY confidence DESC",
            (entity_id,),
        ).fetchall()
        return [_row_to_attribute(r) for r in rows]

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    async def link_entities(
        self,
        from_id: str,
        to_id: str,
        relation_type: str,
        strength: float = 0.5,
        source: str = "llm_inference",
    ) -> None:
        await asyncio.to_thread(
            self._link_entities_sync, from_id, to_id, relation_type, strength, source
        )

    def _link_entities_sync(
        self, from_id: str, to_id: str, relation_type: str,
        strength: float, source: str
    ) -> None:
        now = time.time()
        existing = self._conn.execute(
            "SELECT * FROM relationships WHERE from_entity = ? AND to_entity = ? AND relation_type = ?",
            (from_id, to_id, relation_type),
        ).fetchone()

        if existing:
            # Strengthen existing relationship
            new_strength = min(1.0, existing["strength"] + 0.05)
            self._conn.execute(
                "UPDATE relationships SET strength = ?, last_seen = ? WHERE id = ?",
                (new_strength, now, existing["id"]),
            )
        else:
            self._conn.execute(
                """INSERT INTO relationships
                   (from_entity, to_entity, relation_type, strength, first_seen, last_seen, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (from_id, to_id, relation_type, strength, now, now, source),
            )
        self._conn.commit()

    # ------------------------------------------------------------------
    # World Events
    # ------------------------------------------------------------------

    async def record_event(self, event: WorldEvent) -> int:
        return await asyncio.to_thread(self._record_event_sync, event)

    def _record_event_sync(self, event: WorldEvent) -> int:
        cur = self._conn.execute(
            """INSERT INTO world_events
               (event_type, source, payload, processed, entities_affected, recorded_at)
               VALUES (?, ?, ?, 0, ?, ?)""",
            (event.event_type, event.source, event.payload_json(),
             json.dumps(event.entities_affected), event.recorded_at),
        )
        self._conn.commit()
        return cur.lastrowid

    async def ingest_event(self, event: WorldEvent) -> list[Entity]:
        """Record the event and extract entities from it."""
        from atlas.world.updater import WorldModelUpdater
        event_id = await self.record_event(event)
        updater = WorldModelUpdater(self)
        entities = await updater.process_event(event)
        # Mark processed
        await asyncio.to_thread(
            self._conn.execute,
            "UPDATE world_events SET processed = 1, processed_at = ?, entities_affected = ? WHERE id = ?",
            (time.time(), json.dumps([e.id for e in entities]), event_id),
        )
        await asyncio.to_thread(self._conn.commit)
        return entities

    async def get_unprocessed_events(self, limit: int = 50) -> list[WorldEvent]:
        return await asyncio.to_thread(self._get_unprocessed_sync, limit)

    def _get_unprocessed_sync(self, limit: int) -> list[WorldEvent]:
        rows = self._conn.execute(
            "SELECT * FROM world_events WHERE processed = 0 ORDER BY recorded_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    # ------------------------------------------------------------------
    # Confidence decay
    # ------------------------------------------------------------------

    async def decay_confidence(self, days_threshold: float = 30.0) -> int:
        """Decay confidence of entities not reinforced in `days_threshold` days.
        Returns count of entities decayed.
        """
        return await asyncio.to_thread(self._decay_sync, days_threshold)

    def _decay_sync(self, days_threshold: float) -> int:
        cutoff = time.time() - (days_threshold * 86_400)
        rows = self._conn.execute(
            "SELECT id, confidence FROM entities WHERE last_reinforced < ?",
            (cutoff,),
        ).fetchall()
        count = 0
        for row in rows:
            new_conf = max(0.1, row["confidence"] * 0.9)  # decay 10%, floor at 0.1
            self._conn.execute(
                "UPDATE entities SET confidence = ? WHERE id = ?",
                (new_conf, row["id"]),
            )
            count += 1
        if count:
            self._conn.commit()
        return count

    # ------------------------------------------------------------------
    # Context assembly (delegates to ContextAssembler)
    # ------------------------------------------------------------------

    async def get_context_for_query(
        self, query: str, token_budget: int = 2000
    ) -> WorldContext:
        from atlas.world.assembler import ContextAssembler
        assembler = ContextAssembler(self)
        return await assembler.assemble(query, token_budget)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        import time as _time
        start = _time.monotonic()
        try:
            entity_count = self._conn.execute(
                "SELECT COUNT(*) as n FROM entities"
            ).fetchone()["n"]
            event_count = self._conn.execute(
                "SELECT COUNT(*) as n FROM world_events WHERE processed = 0"
            ).fetchone()["n"]
            return {
                "status": "healthy",
                "last_check": time.time(),
                "elapsed_ms": round((_time.monotonic() - start) * 1000, 2),
                "details": {
                    "entity_count": entity_count,
                    "unprocessed_events": event_count,
                    "db_path": str(self._db_path),
                },
            }
        except Exception as e:
            return {
                "status": "down",
                "last_check": time.time(),
                "elapsed_ms": round((_time.monotonic() - start) * 1000, 2),
                "details": {"error": str(e)},
            }

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------
# Row → dataclass converters
# ------------------------------------------------------------------

def _row_to_entity(row: sqlite3.Row) -> Entity:
    meta_raw = row["metadata"] if "metadata" in row.keys() else "{}"
    try:
        meta = json.loads(meta_raw)
    except (json.JSONDecodeError, TypeError):
        meta = {}
    return Entity(
        id=row["id"],
        type=row["type"],
        name=row["name"],
        canonical_name=row["canonical_name"],
        confidence=row["confidence"],
        first_seen=row["first_seen"],
        last_updated=row["last_updated"],
        last_reinforced=row["last_reinforced"],
        source=row["source"],
        metadata=meta,
        embedding=row["embedding"] if "embedding" in row.keys() else None,
    )


def _row_to_attribute(row: sqlite3.Row) -> Attribute:
    return Attribute(
        id=row["id"],
        entity_id=row["entity_id"],
        key=row["key"],
        value=row["value"],
        confidence=row["confidence"],
        source=row["source"],
        recorded_at=row["recorded_at"],
        superseded_by=row["superseded_by"],
    )


def _row_to_event(row: sqlite3.Row) -> WorldEvent:
    try:
        payload = json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError):
        payload = {}
    try:
        affected = json.loads(row["entities_affected"])
    except (json.JSONDecodeError, TypeError):
        affected = []
    return WorldEvent(
        id=row["id"],
        event_type=row["event_type"],
        source=row["source"],
        payload=payload,
        processed=bool(row["processed"]),
        processed_at=row["processed_at"],
        entities_affected=affected,
        recorded_at=row["recorded_at"],
    )
