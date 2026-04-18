"""IngestionPipeline — chunk, dedup, embed, and store memories.

Chunking: 512-token chunks with 50-token overlap.
Dedup: skip if >0.95 cosine similarity to existing memory.
Entity linking: extract entities and store entity_ids in metadata.
Embedding: generate vector via SemanticStore, stored in embeddings table.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import struct
import time
import uuid
from typing import Any

from atlas.core.models import MemoryEntry

logger = logging.getLogger("atlas.rag.ingestion")

DIMS = 384
CHUNK_TOKENS = 512
OVERLAP_TOKENS = 50
CHARS_PER_TOKEN = 4
DEDUP_THRESHOLD = 0.95

# Approximate chars per chunk
_CHUNK_CHARS = CHUNK_TOKENS * CHARS_PER_TOKEN      # 2048
_OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN   # 200


class IngestionPipeline:
    def __init__(self, memory_store, world_model=None) -> None:
        self._mem = memory_store
        self._world = world_model
        import sqlite3 as _sqlite3
        db_path = str(memory_store.db.execute("PRAGMA database_list").fetchone()[2])
        self._db = _sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = _sqlite3.Row

    async def ingest(
        self,
        source: str,
        content: str,
        metadata: dict | None = None,
        mem_type: str = "general",
    ) -> list[str]:
        """Ingest content → chunk → dedup → embed → store. Returns list of memory IDs stored."""
        if not content or not content.strip():
            return []

        chunks = _chunk_text(content)
        stored_ids: list[str] = []

        for chunk in chunks:
            mem_id = await asyncio.to_thread(
                self._ingest_chunk_sync, chunk, source, metadata or {}, mem_type
            )
            if mem_id:
                stored_ids.append(mem_id)

        return stored_ids

    def _ingest_chunk_sync(
        self, chunk: str, source: str, metadata: dict, mem_type: str
    ) -> str | None:
        sem = getattr(self._mem, "semantic", None)

        # Generate embedding for dedup check
        vec_bytes = sem.encode(chunk) if sem else None

        # Dedup: check cosine against recent embeddings
        if vec_bytes and self._is_duplicate(vec_bytes):
            logger.debug("Skipping duplicate chunk (%.2f+ cosine)", DEDUP_THRESHOLD)
            return None

        # Extract entity IDs for linking
        entity_ids: list[str] = []
        if self._world:
            from atlas.world.extractor import extract_from_text
            mentions = extract_from_text(chunk[:500], source=source)
            for mention in mentions[:5]:
                entity = self._world._get_entity_sync(mention.name)
                if entity:
                    entity_ids.append(entity.id)

        # Build metadata
        full_meta = {
            **metadata,
            "source": source,
            "entity_ids": entity_ids,
            "chunk_len": len(chunk),
        }

        # Store in memories table
        mem_id = uuid.uuid4().hex
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            self._db.execute(
                "INSERT INTO memories "
                "(id, type, content, source, confidence, importance, access_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (mem_id, mem_type, chunk, source, 0.8, 0.5, now_iso, now_iso),
            )
            self._db.commit()
        except Exception as e:
            logger.error("Memory insert failed: %s", e)
            return None

        # Store embedding
        if sem and vec_bytes:
            try:
                sem.add(mem_id, "memory", chunk, metadata=full_meta)
            except Exception as e:
                logger.warning("Embedding store failed: %s", e)

        return mem_id

    def _is_duplicate(self, vec_bytes: bytes) -> bool:
        """Return True if any existing embedding has cosine >= DEDUP_THRESHOLD."""
        try:
            rows = self._db.execute(
                "SELECT vector FROM embeddings WHERE source = 'memory' ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        except Exception:
            return False

        q = struct.unpack(f"{DIMS}f", vec_bytes)
        for row in rows:
            if not row["vector"]:
                continue
            try:
                r = struct.unpack(f"{DIMS}f", row["vector"])
                cos = _cosine(q, r)
                if cos >= DEDUP_THRESHOLD:
                    return True
            except struct.error:
                continue
        return False


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks of ~CHUNK_CHARS with OVERLAP_CHARS overlap."""
    if len(text) <= _CHUNK_CHARS:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_CHARS
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Find a natural break point (sentence or paragraph)
        break_at = _find_break(text, end)
        chunks.append(text[start:break_at])
        start = break_at - _OVERLAP_CHARS  # overlap
        if start < 0:
            start = 0
    return [c.strip() for c in chunks if c.strip()]


def _find_break(text: str, near: int) -> int:
    """Find a sentence boundary near `near` position."""
    window = 200
    for sep in ["\n\n", ".\n", ". ", "! ", "? ", "\n", " "]:
        idx = text.rfind(sep, max(0, near - window), near + window)
        if idx != -1:
            return idx + len(sep)
    return near


def _cosine(a: tuple, b: tuple) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
