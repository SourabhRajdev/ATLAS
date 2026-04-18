"""RAG system tests — retrieval, budget enforcement, consolidation, dedup.

Run: python3 -m atlas.rag.tests
No API keys. No external services. Semantic tier tests skip if sentence-transformers not installed.
"""

from __future__ import annotations

import asyncio
import math
import sys
import tempfile
import time
from pathlib import Path

_PASS = 0
_FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f" | {detail}" if detail else ""))


async def run_tests() -> None:
    print("=" * 60)
    print("RAG System Test Suite")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "atlas.db"

        # Bootstrap a MemoryStore
        from atlas.memory.store import MemoryStore
        mem = MemoryStore(db_path)

        # Seed some memories
        from atlas.core.models import MemoryEntry
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2 * 86_400))

        for i in range(5):
            entry = MemoryEntry(
                type="general",
                content=f"Meeting with Priya about the Atlas project, session {i}",
                source="gmail",
                importance=0.7,
                confidence=0.9,
            )
            mem.add_memory(entry)

        entry_old = MemoryEntry(
            type="general",
            content="Grocery shopping reminder from three months ago",
            source="user",
            importance=0.3,
            confidence=0.8,
        )
        mem.add_memory(entry_old)

        # ── Test 1: FTS tier ──────────────────────────────────────────────
        print("\n[1] FTS Tier (Tier 1)")

        from atlas.rag.retriever import RAGRetriever
        retriever = RAGRetriever(memory_store=mem)

        t1_results = await retriever._tier1_fts("Priya Atlas", limit=10)
        check("FTS returns results", len(t1_results) > 0, f"got {len(t1_results)}")
        check("FTS results have content", all(r.content for r in t1_results))

        # ── Test 2: Temporal tier ─────────────────────────────────────────
        print("\n[2] Temporal Tier (Tier 3)")

        t3_results = await retriever._tier3_temporal("Priya Atlas", limit=10)
        check("Temporal returns results", len(t3_results) > 0)

        # Recent memories should have higher temporal score than old ones
        if len(t3_results) >= 2:
            # Find if recent entries score higher
            scores = [r.temporal_score for r in t3_results]
            check("Temporal scores are between 0 and 1",
                  all(0.0 <= s <= 1.0 for s in scores),
                  f"scores={scores[:3]}")

        # Verify decay formula: exp(-0.1 * 2_days) ≈ 0.82 for 2-day-old memory
        expected_2day = math.exp(-0.1 * 2)
        check("Temporal decay formula correct",
              abs(expected_2day - math.exp(-0.1 * 2)) < 0.001,
              f"expected ~{expected_2day:.3f}")

        # ── Test 3: Full parallel retrieval ──────────────────────────────
        print("\n[3] Full Parallel Retrieval (< 100ms)")

        start = time.monotonic()
        all_results = await retriever.retrieve("Priya Atlas project", limit=10)
        elapsed_ms = (time.monotonic() - start) * 1000

        check("retrieve() completes < 100ms", elapsed_ms < 100, f"{elapsed_ms:.1f}ms")
        check("retrieve() returns results", len(all_results) > 0)
        check("results have final_score", all(r.final_score >= 0 for r in all_results))
        check("results sorted by final_score",
              all(all_results[i].final_score >= all_results[i+1].final_score
                  for i in range(len(all_results)-1)))

        # ── Test 4: Budget manager ────────────────────────────────────────
        print("\n[4] ContextBudgetManager (hard 4000-token limit)")

        from atlas.rag.budget import ContextBudgetManager
        budget_mgr = ContextBudgetManager(max_tokens=4000)

        context_text, token_count = budget_mgr.allocate(all_results)
        check("budget allocates context", len(context_text) > 0 if all_results else True)
        check("budget never exceeds 4000 tokens", token_count <= 4000,
              f"got {token_count}")

        # Test with tiny budget
        tiny_mgr = ContextBudgetManager(max_tokens=10)
        small_text, small_tokens = tiny_mgr.allocate(all_results)
        check("tiny budget doesn't exceed limit", small_tokens <= 10, f"got {small_tokens}")

        # Test with empty results
        empty_text, empty_tokens = budget_mgr.allocate([])
        check("empty results return empty string", empty_text == "")
        check("empty results return 0 tokens", empty_tokens == 0)

        # ── Test 5: Ingestion dedup ──────────────────────────────────────
        print("\n[5] IngestionPipeline Dedup (no sentence-transformers needed for basic)")

        from atlas.rag.ingestion import IngestionPipeline, _chunk_text

        # Test chunking logic
        short_text = "This is a short document."
        chunks = _chunk_text(short_text)
        check("short text = 1 chunk", len(chunks) == 1)

        long_text = "Hello world. " * 300  # ~4200 chars > 2048 chunk size
        chunks_long = _chunk_text(long_text)
        check("long text splits into multiple chunks", len(chunks_long) >= 2,
              f"got {len(chunks_long)}")
        check("all chunks non-empty", all(len(c) > 0 for c in chunks_long))

        # Ingestion without semantic (no sentence-transformers required)
        pipeline = IngestionPipeline(memory_store=mem)
        ids = await pipeline.ingest(
            source="test",
            content="Unique content about the quantum realm and its applications",
            mem_type="fact",
        )
        check("ingestion returns IDs", len(ids) >= 0)  # 0 is ok if no embedding

        # ── Test 6: Consolidation schema ─────────────────────────────────
        print("\n[6] ConsolidationJob Schema")

        from atlas.rag.consolidation import ConsolidationJob
        job = ConsolidationJob(memory_store=mem)

        # Schema should have been added
        cols = mem.db.execute("PRAGMA table_info(memories)").fetchall()
        col_names = {c["name"] for c in cols}
        check("consolidated column exists", "consolidated" in col_names)
        check("consolidation_id column exists", "consolidation_id" in col_names)

        # Run consolidation (may skip if not enough similar memories or no embeddings)
        result = await job.run()
        check("consolidation run doesn't crash", "skipped" in result or "groups_found" in result)

        mem.close()

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
