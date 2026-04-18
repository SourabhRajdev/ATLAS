"""Memory consolidation — weekly background job.

If 5+ memories have cosine similarity > 0.85:
  → Summarize into 1 consolidated memory
  → Mark originals as consolidated=True (never delete — audit chain)
  → Store consolidation_id linking originals to the new memory

Does NOT use LLM for consolidation (no API cost). Uses extractive
summarization: pick the most informative chunk (highest importance)
as the consolidation anchor, append unique sentences from the rest.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("atlas.rag.consolidation")

DIMS = 384
SIMILARITY_THRESHOLD = 0.85
MIN_GROUP_SIZE = 5


@dataclass
class ConsolidationGroup:
    memory_ids: list[str]
    representative: str  # the consolidated content
    similarity_avg: float


class ConsolidationJob:
    def __init__(self, memory_store) -> None:
        self._mem = memory_store
        import sqlite3 as _sqlite3
        db_path = str(memory_store.db.execute("PRAGMA database_list").fetchone()[2])
        self._db = _sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = _sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Add consolidated flag and consolidation_id column if missing."""
        col_defs = [
            ("consolidated", "INTEGER NOT NULL DEFAULT 0"),
            ("consolidation_id", "TEXT"),
        ]
        for col, defn in col_defs:
            try:
                self._db.execute(f"ALTER TABLE memories ADD COLUMN {col} {defn}")
                self._db.commit()
            except Exception:
                pass  # column already exists

    async def run(self) -> dict:
        """Run consolidation. Returns stats dict."""
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> dict:
        start = time.monotonic()
        sem = getattr(self._mem, "semantic", None)
        if sem is None:
            return {"skipped": True, "reason": "semantic store unavailable"}

        # Load all non-consolidated memories with embeddings
        rows = self._db.execute(
            """SELECT m.id, m.content, m.importance, e.vector
               FROM memories m
               JOIN embeddings e ON e.id = m.id AND e.source = 'memory'
               WHERE m.consolidated = 0
               ORDER BY m.importance DESC""",
        ).fetchall()

        if len(rows) < MIN_GROUP_SIZE:
            return {"skipped": True, "reason": f"only {len(rows)} unconsolidated memories"}

        # Decode vectors
        memories_with_vecs = []
        for row in rows:
            if not row["vector"]:
                continue
            try:
                vec = struct.unpack(f"{DIMS}f", row["vector"])
                memories_with_vecs.append({
                    "id": row["id"],
                    "content": row["content"],
                    "importance": row["importance"],
                    "vec": vec,
                })
            except struct.error:
                continue

        # Cluster by similarity (greedy)
        groups = _greedy_cluster(memories_with_vecs, SIMILARITY_THRESHOLD, MIN_GROUP_SIZE)

        stats = {
            "groups_found": len(groups),
            "memories_consolidated": 0,
            "elapsed_ms": 0.0,
        }

        for group in groups:
            consolidated_id = self._consolidate_group(group, sem)
            if consolidated_id:
                stats["memories_consolidated"] += len(group["ids"])

        stats["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
        return stats

    def _consolidate_group(self, group: dict, sem) -> str | None:
        ids = group["ids"]
        contents = group["contents"]
        importances = group["importances"]

        # Pick highest-importance as anchor
        best_idx = importances.index(max(importances))
        anchor = contents[best_idx]

        # Append unique sentences from others (simple extractive)
        unique_sentences: list[str] = []
        anchor_sentences = set(_sentences(anchor))
        for content in contents:
            if content == anchor:
                continue
            for sent in _sentences(content):
                if sent not in anchor_sentences and len(sent) > 20:
                    unique_sentences.append(sent)
                    anchor_sentences.add(sent)

        consolidated_text = anchor
        for sent in unique_sentences[:3]:  # cap at 3 extra sentences
            consolidated_text += " " + sent

        consolidated_text = consolidated_text[:1000].strip()

        # Insert consolidated memory
        new_id = uuid.uuid4().hex
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            self._db.execute(
                "INSERT INTO memories "
                "(id, type, content, source, confidence, importance, access_count, "
                " created_at, updated_at, consolidated, consolidation_id) "
                "VALUES (?, 'consolidated', ?, 'consolidation', 0.9, 0.8, 0, ?, ?, 0, ?)",
                (new_id, consolidated_text, now_iso, now_iso, new_id),
            )
            # Mark originals as consolidated (never deleted)
            for mem_id in ids:
                self._db.execute(
                    "UPDATE memories SET consolidated = 1, consolidation_id = ? WHERE id = ?",
                    (new_id, mem_id),
                )
            self._db.commit()

            # Embed the consolidated memory
            try:
                sem.add(new_id, "memory", consolidated_text)
            except Exception as e:
                logger.warning("Failed to embed consolidated memory: %s", e)

            logger.info("Consolidated %d memories into %s", len(ids), new_id)
            return new_id
        except Exception as e:
            logger.error("Consolidation insert failed: %s", e)
            return None


def _greedy_cluster(
    memories: list[dict],
    threshold: float,
    min_size: int,
) -> list[dict]:
    """Simple greedy clustering: pick seed, find all similar, remove from pool."""
    pool = list(range(len(memories)))
    groups = []

    while len(pool) >= min_size:
        seed_idx = pool[0]
        seed_vec = memories[seed_idx]["vec"]
        cluster_indices = [seed_idx]

        remaining = []
        for i in pool[1:]:
            cos = _cosine(seed_vec, memories[i]["vec"])
            if cos >= threshold:
                cluster_indices.append(i)
            else:
                remaining.append(i)

        if len(cluster_indices) >= min_size:
            groups.append({
                "ids": [memories[i]["id"] for i in cluster_indices],
                "contents": [memories[i]["content"] for i in cluster_indices],
                "importances": [memories[i]["importance"] for i in cluster_indices],
            })
            pool = remaining
        else:
            pool = pool[1:] + [seed_idx]  # move seed to back to avoid infinite loop
            if not remaining:
                break

    return groups


def _sentences(text: str) -> list[str]:
    import re
    return [s.strip() for s in re.split(r'[.!?]\s+', text) if s.strip()]


def _cosine(a: tuple, b: tuple) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
