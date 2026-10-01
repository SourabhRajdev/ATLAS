"""4-tier parallel RAG retriever.

Tier 1: Exact FTS5 match (< 5ms)
Tier 2: Semantic vector cosine (< 50ms)
Tier 3: Temporal decay scoring (< 10ms)
Tier 4: Relational / entity-linked (< 20ms)

All tiers queried in parallel via asyncio.gather.
Results merged and scored into a unified ranked list.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("atlas.rag.retriever")

# Decay constants per memory type
_LAMBDA = {
    "event": 0.3,    # meetings, emails — decay fast
    "general": 0.1,  # general memories
    "fact": 0.05,    # explicit facts ("my phone is...") — decay slow
}


@dataclass
class RankedResult:
    id: str
    content: str
    type: str
    source: str
    created_at: str
    importance: float = 0.5
    fts_score: float = 0.0
    semantic_score: float = 0.0
    temporal_score: float = 0.0
    relational_score: float = 0.0
    final_score: float = 0.0
    entity_ids: list[str] = field(default_factory=list)


class RAGRetriever:
    def __init__(
        self,
        memory_store,    # atlas.memory.store.MemoryStore
        world_model=None,  # atlas.world.WorldModel (optional)
    ) -> None:
        self._mem = memory_store
        self._world = world_model
        # Open a separate thread-safe connection to the same DB file
        # so asyncio.to_thread workers don't hit check_same_thread errors.
        import sqlite3 as _sqlite3
        db_path = str(memory_store.db.execute("PRAGMA database_list").fetchone()[2])
        self._db = _sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = _sqlite3.Row

    async def retrieve(
        self,
        query: str,
        limit: int = 20,
        include_types: list[str] | None = None,
    ) -> list[RankedResult]:
        """Query all 4 tiers in parallel and return merged ranked results."""
        t1, t2, t3, t4 = await asyncio.gather(
            self._tier1_fts(query, limit),
            self._tier2_semantic(query, limit),
            self._tier3_temporal(query, limit),
            self._tier4_relational(query, limit),
        )

        merged = _merge_results(t1, t2, t3, t4)

        # Reciprocal Rank Fusion instead of a fixed weighted sum of raw scores.
        # The old scheme added fts_score/semantic_score/temporal_score/
        # relational_score directly — four numbers from different, mutually
        # incomparable distributions (FTS rank-derived, cosine similarity,
        # exponential decay, a hardcoded constant), so the "0.35/0.30/0.20/
        # 0.15" weights were tuning noise, not a real calibration. RRF only
        # needs each tier's own internal ranking, which is robust regardless
        # of what scale a tier's raw score happens to live on.
        rrf_scores = _reciprocal_rank_fusion([t1, t2, t3, t4])
        now = time.time()
        for r in merged:
            fused = rrf_scores.get(r.id, 0.0)
            # Recency and importance as small multipliers AFTER fusion, not
            # folded into it — a very recent, low-importance result shouldn't
            # outrank a highly-ranked, important one just because of age.
            r.final_score = fused * _recency_multiplier(r, now) * (0.5 + 0.5 * r.importance)

        merged.sort(key=lambda r: r.final_score, reverse=True)
        return merged[:limit]

    # ------------------------------------------------------------------
    # Tier 1: FTS5 exact match
    # ------------------------------------------------------------------

    async def _tier1_fts(self, query: str, limit: int) -> list[RankedResult]:
        return await asyncio.to_thread(self._tier1_fts_sync, query, limit)

    def _tier1_fts_sync(self, query: str, limit: int) -> list[RankedResult]:
        # Build FTS query: each word is a separate term (OR semantics)
        # Phrase matching is too strict for natural language queries.
        terms = [w.replace('"', '""') for w in query.split() if w.strip()]
        fts_query = " OR ".join(terms) if terms else query
        try:
            rows = self._db.execute(
                """SELECT m.id, m.type, m.content, m.importance,
                          m.created_at, m.source, rank
                   FROM memories_fts f
                   JOIN memories m ON f.rowid = m.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        except Exception as e:
            logger.debug("FTS tier1 fallback: %s", e)
            rows = self._db.execute(
                "SELECT id, type, content, importance, created_at, source, 0.0 as rank "
                "FROM memories WHERE content LIKE ? LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()

        results = []
        for row in rows:
            r = RankedResult(
                id=row["id"],
                content=row["content"],
                type=row["type"],
                source=row["source"] if "source" in row.keys() else "",
                created_at=str(row["created_at"]),
                importance=float(row["importance"]),
            )
            # FTS rank is negative (better = more negative), normalize to 0-1
            raw_rank = float(row["rank"]) if row["rank"] else 0.0
            r.fts_score = max(0.0, min(1.0, 1.0 / (1.0 + abs(raw_rank))))
            results.append(r)
        return results

    # ------------------------------------------------------------------
    # Tier 2: Semantic vector cosine
    # ------------------------------------------------------------------

    async def _tier2_semantic(self, query: str, limit: int) -> list[RankedResult]:
        return await asyncio.to_thread(self._tier2_semantic_sync, query, limit)

    def _tier2_semantic_sync(self, query: str, limit: int) -> list[RankedResult]:
        if not hasattr(self._mem, "semantic") or self._mem.semantic is None:
            return []
        sem = self._mem.semantic

        # Delegates to SemanticStore's NumPy in-RAM matrix search (one matmul,
        # not a per-row Python cosine loop — see atlas/memory/semantic.py).
        # Oversample (limit*3, same pattern tier3 already uses below) since we
        # still filter by a minimum-similarity floor afterward — some of the
        # raw top-N might fall under it.
        #
        # This also removes a scalability bug the old inline version had: it
        # queried "... LIMIT 500" with no ORDER BY, so past 500 embeddings it
        # silently searched an arbitrary (usually insertion-order) subset
        # instead of the whole table. SemanticStore now searches everything.
        raw = sem.search(query, source="memory", limit=limit * 3)
        if not raw:
            return []

        ids = [r["id"] for r in raw]
        placeholders = ",".join("?" * len(ids))
        meta_rows = self._db.execute(
            f"SELECT id, type, importance, created_at, source FROM memories "
            f"WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        meta_by_id = {row["id"]: row for row in meta_rows}

        _MIN_SIMILARITY = 0.3  # same floor the old inline cosine loop used
        scored = []
        for r in raw:
            if r["score"] <= _MIN_SIMILARITY:
                continue
            meta = meta_by_id.get(r["id"])
            scored.append(RankedResult(
                id=r["id"],
                content=r["text"],
                type=(meta["type"] if meta else None) or "general",
                source=(meta["source"] if meta else r["source"]) or "",
                created_at=str(meta["created_at"]) if meta else "",
                importance=float(meta["importance"]) if meta else 0.5,
                semantic_score=r["score"],
            ))
        return scored[:limit]

    # ------------------------------------------------------------------
    # Tier 3: Temporal decay
    # ------------------------------------------------------------------

    async def _tier3_temporal(self, query: str, limit: int) -> list[RankedResult]:
        return await asyncio.to_thread(self._tier3_temporal_sync, query, limit)

    def _tier3_temporal_sync(self, query: str, limit: int) -> list[RankedResult]:
        # FTS pre-filter to get candidate set, then apply temporal scoring
        terms = [w.replace('"', '""') for w in query.split() if w.strip()]
        fts_query = " OR ".join(terms) if terms else query
        try:
            rows = self._db.execute(
                """SELECT m.id, m.type, m.content, m.importance,
                          m.created_at, m.source, rank
                   FROM memories_fts f
                   JOIN memories m ON f.rowid = m.rowid
                   WHERE memories_fts MATCH ?
                   LIMIT ?""",
                (fts_query, limit * 3),
            ).fetchall()
        except Exception:
            rows = self._db.execute(
                "SELECT id, type, content, importance, created_at, source, 0.0 as rank "
                "FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit * 3,),
            ).fetchall()

        now = time.time()
        results = []
        for row in rows:
            mem_type = row["type"] or "general"
            lam = _LAMBDA.get(mem_type, _LAMBDA["general"])
            created_at_str = str(row["created_at"])
            try:
                from datetime import datetime, timezone
                if "T" in created_at_str or "+" in created_at_str:
                    dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    created_ts = dt.timestamp()
                else:
                    created_ts = float(created_at_str)
            except (ValueError, TypeError):
                created_ts = now - 86_400  # assume 1 day old on parse fail

            days_old = max(0.0, (now - created_ts) / 86_400)
            temporal_score = math.exp(-lam * days_old)

            rr = RankedResult(
                id=row["id"],
                content=row["content"],
                type=mem_type,
                source=row["source"] if "source" in row.keys() else "",
                created_at=created_at_str,
                importance=float(row["importance"]),
                temporal_score=temporal_score,
            )
            results.append(rr)

        results.sort(key=lambda r: r.temporal_score, reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # Tier 4: Relational (entity-linked)
    # ------------------------------------------------------------------

    async def _tier4_relational(self, query: str, limit: int) -> list[RankedResult]:
        if self._world is None:
            return []
        return await asyncio.to_thread(self._tier4_relational_sync, query, limit)

    def _tier4_relational_sync(self, query: str, limit: int) -> list[RankedResult]:
        # Find entities mentioned in query
        from atlas.world.extractor import extract_from_text
        mentions = extract_from_text(query, source="user")
        if not mentions:
            return []

        entity_ids = []
        for mention in mentions[:3]:  # top 3 mentions
            found = self._world._get_entity_sync(mention.name)
            if found:
                entity_ids.append(found.id)

        if not entity_ids:
            return []

        # Find memories linked to these entities
        # (via the memories.source field containing entity info, or via tags)
        results = []
        for entity_id in entity_ids:
            rows = self._db.execute(
                "SELECT id, type, content, importance, created_at, source "
                "FROM memories WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{entity_id}%", limit),
            ).fetchall()
            for row in rows:
                rr = RankedResult(
                    id=row["id"],
                    content=row["content"],
                    type=row["type"],
                    source=row["source"] if "source" in row.keys() else "",
                    created_at=str(row["created_at"]),
                    importance=float(row["importance"]),
                    relational_score=0.8,
                    entity_ids=[entity_id],
                )
                results.append(rr)

        return results[:limit]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_RRF_K = 60  # standard choice; needs no per-corpus tuning


def _reciprocal_rank_fusion(tier_lists: list[list[RankedResult]], k: int = _RRF_K) -> dict[str, float]:
    """score(d) = sum over tiers d appears in of 1/(k + rank_in_that_tier).

    Each tier list is assumed already sorted best-first (true for all 4
    tiers here). Normalized by the maximum possible score (appearing 1st in
    every tier) so the result stays in a 0-1-ish range comparable to the old
    weighted-sum scheme — callers downstream (ContextBudgetManager's
    MIN_SCORE drop threshold) depend on that range, and an unnormalized RRF
    score is small enough (~0.016 for a single rank-1 hit, vs MIN_SCORE=0.05)
    that it would silently drop results that used to survive.
    """
    raw: dict[str, float] = {}
    for tier in tier_lists:
        for rank, r in enumerate(tier, start=1):
            raw[r.id] = raw.get(r.id, 0.0) + 1.0 / (k + rank)

    max_possible = len(tier_lists) * (1.0 / (k + 1))
    if max_possible <= 0:
        return raw
    return {doc_id: score / max_possible for doc_id, score in raw.items()}


def _recency_multiplier(r: "RankedResult", now: float) -> float:
    """Mild post-fusion recency boost — same per-type decay rates tier 3
    already uses, but applied as a 0.7-1.0 multiplier (not a 0-1 score) so
    an old-but-highly-ranked result isn't crushed just for being old."""
    try:
        s = r.created_at
        if "T" in s or "+" in s:
            from datetime import datetime
            created_ts = datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        else:
            created_ts = float(s)
    except (ValueError, TypeError):
        return 1.0  # unknown age — don't penalize
    days_old = max(0.0, (now - created_ts) / 86_400)
    lam = _LAMBDA.get(r.type, _LAMBDA["general"])
    decay = math.exp(-lam * days_old)
    return 0.7 + 0.3 * decay


def _merge_results(
    t1: list[RankedResult],
    t2: list[RankedResult],
    t3: list[RankedResult],
    t4: list[RankedResult],
) -> list[RankedResult]:
    """Merge 4 tier results by ID, accumulating scores."""
    merged: dict[str, RankedResult] = {}

    for result_list, score_attr in [
        (t1, "fts_score"),
        (t2, "semantic_score"),
        (t3, "temporal_score"),
        (t4, "relational_score"),
    ]:
        for r in result_list:
            if r.id in merged:
                existing = merged[r.id]
                setattr(existing, score_attr, max(
                    getattr(existing, score_attr),
                    getattr(r, score_attr),
                ))
                existing.entity_ids.extend(r.entity_ids)
            else:
                merged[r.id] = r

    return list(merged.values())
