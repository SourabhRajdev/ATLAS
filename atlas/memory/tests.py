"""Memory system tests — SemanticStore's NumPy in-RAM vector search (Phase 1 Step E).

Run: python3 -m atlas.memory.tests
No API keys. No external services. No sentence-transformers needed — uses a
deterministic fake embedding (seeded random projection of word hashes) so the
correctness and performance tests don't depend on the optional ML model being
installed, matching the rest of the suite's "no API keys, no external
services" convention. A real-embedding relevance check runs too, but only if
sentence-transformers happens to be installed, and is skipped (not failed)
otherwise — same pattern atlas/rag/tests.py already uses.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

_PASS = 0
_FAIL = 0
_SKIP = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f" | {detail}" if detail else ""))


def skip(name: str, reason: str) -> None:
    global _SKIP
    _SKIP += 1
    print(f"  SKIP  {name} | {reason}")


# --------------------------------------------------------------------------- #
# Deterministic fake embedding — a seeded random projection of word hashes.
# Shared vocabulary between two texts pulls their vectors together (same
# word -> same random vector -> higher cosine on overlap), which is enough
# to test that search() actually ranks by similarity, without needing the
# real ~60MB model.
# --------------------------------------------------------------------------- #

def _word_vector(word: str, dims: int) -> np.ndarray:
    seed = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
    rng = np.random.RandomState(seed)
    return rng.standard_normal(dims).astype(np.float32)


def _fake_embed(text: str, dims: int) -> np.ndarray:
    words = text.lower().split() or [text.lower()]
    v = np.mean([_word_vector(w, dims) for w in words], axis=0)
    norm = np.linalg.norm(v)
    return (v / norm).astype(np.float32) if norm > 0 else v.astype(np.float32)


def run_tests() -> None:
    print("=" * 60)
    print("Memory System Test Suite")
    print("=" * 60)

    from atlas.memory.semantic import SemanticStore, DIMS

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "atlas.db"
        conn = sqlite3.connect(str(db_path))
        store = SemanticStore(conn)
        # Swap in the deterministic fake embedder — see module docstring.
        store._encode_vector = lambda text: _fake_embed(text, DIMS)

        # ── Test 1: add/search/delete round-trip through the public API ────
        print("\n[1] add() / search() / delete() round-trip")

        check("add() returns True", store.add("m1", "memory", "the quick brown fox"))
        check("add() a second item", store.add("m2", "memory", "a slow red turtle"))
        check("add() a third item, different source", store.add("m3", "other", "the quick brown fox jumps"))

        results = store.search("quick brown fox", limit=5)
        check("search() returns results", len(results) > 0)
        ids = [r["id"] for r in results]
        check("most similar text ranks first", ids[0] in ("m1", "m3"), f"got order {ids}")
        check("results are sorted by score descending",
              all(results[i]["score"] >= results[i + 1]["score"] for i in range(len(results) - 1)))

        filtered = store.search("quick brown fox", source="memory", limit=5)
        check("source filter excludes the other-source item", all(r["source"] == "memory" for r in filtered))
        check("source filter still finds the matching memory item", any(r["id"] == "m1" for r in filtered))

        store.delete("m1")
        after_delete = store.search("quick brown fox", limit=5)
        check("deleted item no longer appears in search results", all(r["id"] != "m1" for r in after_delete))

        # ── Test 2: add() with the same id replaces, doesn't duplicate ──────
        print("\n[2] add() with an existing id replaces in place")

        store.add("m2", "memory", "a slow red turtle, now with more text")
        dupe_check = store.search("turtle", limit=10)
        m2_count = sum(1 for r in dupe_check if r["id"] == "m2")
        check("re-adding the same id doesn't create a duplicate row in the matrix", m2_count == 1, f"got {m2_count}")
        m2_result = next(r for r in dupe_check if r["id"] == "m2")
        check("re-adding the same id updates its stored text", "now with more text" in m2_result["text"])

        # ── Test 3: in-RAM matrix mirrors the DB after a process restart ────
        print("\n[3] A fresh SemanticStore loads existing embeddings from disk")

        store2 = SemanticStore(conn)
        store2._encode_vector = lambda text: _fake_embed(text, DIMS)
        reloaded = store2.search("quick brown fox", limit=5)
        check("a new SemanticStore instance sees previously-stored embeddings",
              any(r["id"] == "m3" for r in reloaded), f"got {[r['id'] for r in reloaded]}")

        conn.close()

    # ── Test 4: performance benchmark — the actual Step E exit check ───────
    # "Retrieval p95 < 30ms at 50k" (PHASE_1_BRIEF.md). Populates the matrix
    # directly with synthetic vectors (bypassing per-row DB round trips and
    # the embedding model) so this measures exactly what Step E changed —
    # the search/matmul path — not insertion speed or model-inference speed,
    # which are separate, pre-existing costs this step doesn't touch.
    print("\n[4] Performance: search_by_vector() at 50,000 rows")

    with tempfile.TemporaryDirectory() as tmpdir:
        bench_conn = sqlite3.connect(str(Path(tmpdir) / "bench.db"))
        bench_store = SemanticStore(bench_conn)

        n = 50_000
        rng = np.random.RandomState(42)
        matrix = rng.standard_normal((n, DIMS)).astype(np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)

        bench_store._ids = [f"bench-{i}" for i in range(n)]
        bench_store._sources = ["memory"] * n
        bench_store._texts = [""] * n
        bench_store._metadata = [{}] * n
        bench_store._matrix = matrix
        bench_store._matrix_loaded = True

        query_vecs = rng.standard_normal((50, DIMS)).astype(np.float32)
        query_vecs /= np.linalg.norm(query_vecs, axis=1, keepdims=True)

        latencies_ms = []
        for qv in query_vecs:
            t0 = time.perf_counter()
            bench_store.search_by_vector(qv, source="memory", limit=20)
            latencies_ms.append((time.perf_counter() - t0) * 1000)

        latencies_ms.sort()
        p50 = latencies_ms[len(latencies_ms) // 2]
        p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
        print(f"  n=50,000 rows, {len(latencies_ms)} queries: p50={p50:.2f}ms p95={p95:.2f}ms "
              f"max={max(latencies_ms):.2f}ms")
        check("p95 search latency < 30ms at 50k rows (Step E exit check)", p95 < 30.0, f"got {p95:.2f}ms")

        bench_conn.close()

    # ── Test 5 (optional): real embeddings, only if sentence-transformers is
    # actually installed — never required, never faked as passing.
    print("\n[5] Real-embedding relevance check (optional)")
    try:
        import sentence_transformers  # noqa: F401
        has_real_model = True
    except ImportError:
        has_real_model = False

    if not has_real_model:
        skip("real embeddings rank semantically similar text higher", "sentence-transformers not installed")
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            real_conn = sqlite3.connect(str(Path(tmpdir) / "real.db"))
            real_store = SemanticStore(real_conn)
            real_store.add("r1", "memory", "The quarterly revenue report shows strong growth.")
            real_store.add("r2", "memory", "My dog loves playing fetch in the park.")
            results = real_store.search("How did the company perform financially this quarter?", limit=2)
            check("real embeddings rank the financially-related text above the unrelated one",
                  bool(results) and results[0]["id"] == "r1", f"got {[r['id'] for r in results]}")
            real_conn.close()

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)")
          + (f"  [{_SKIP} skipped]" if _SKIP else ""))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    run_tests()
