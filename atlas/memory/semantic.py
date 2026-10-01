"""Semantic memory — local embeddings for vector search.

Uses sentence-transformers/bge-small-en-v1.5 (33M params, ~60MB). Vectors are
stored durably in SQLite as blobs, and mirrored in a normalised float32 NumPy
matrix held in RAM for search: one matmul per query instead of a Python loop
computing cosine similarity row by row. Target: <10ms at 50k rows (see
docs/decisions/001-context-engine-and-memory.md Section 3.4).

The embedding model and the in-RAM matrix both load lazily on first use, so a
store that's never searched doesn't pay either cost. On M-series ~15ms/embed.
"""

from __future__ import annotations

import json
import logging
import struct
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("atlas.memory.semantic")

DIMS = 384  # bge-small output


def _pack(vec: np.ndarray) -> bytes:
    return struct.pack(f"{DIMS}f", *vec.tolist())


def _unpack(blob: bytes) -> np.ndarray:
    return np.array(struct.unpack(f"{DIMS}f", blob), dtype=np.float32)


class SemanticStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        # A dedicated check_same_thread=False connection to the SAME db file,
        # not the connection passed in. SemanticStore's callers run it from
        # asyncio.to_thread workers (RAGRetriever's tier2, IngestionPipeline's
        # per-chunk embed) on a different OS thread than whatever thread
        # constructed MemoryStore — the passed-in `db` is thread-affine
        # (sqlite3.connect's default), so using it directly here raises
        # "SQLite objects created in a thread can only be used in that same
        # thread" the moment a worker thread touches it. This was already
        # true for add() before this change (IngestionPipeline calls it from
        # a to_thread worker) but silently swallowed by a broad except —
        # every embedding write from ingestion was failing, just never
        # loudly, because the test environment has no sentence-transformers
        # installed and so never reached that code path. Same pattern
        # RAGRetriever/IngestionPipeline/ConsolidationJob already use for
        # exactly this reason.
        db_path = str(db.execute("PRAGMA database_list").fetchone()[2])
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._model = None
        self._init_tables()

        # In-RAM mirror of the embeddings table. Parallel lists + one matrix,
        # indexed consistently by position — kept in sync incrementally by
        # add()/delete() after the first load, not rebuilt from scratch on
        # every write.
        self._ids: list[str] = []
        self._sources: list[str] = []
        self._texts: list[str] = []
        self._metadata: list[dict] = []
        self._matrix: np.ndarray = np.zeros((0, DIMS), dtype=np.float32)
        self._matrix_loaded = False

    def _init_tables(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS embeddings (
                id          TEXT PRIMARY KEY,
                source      TEXT NOT NULL,       -- "memory", "world_snapshot", "action"
                text        TEXT NOT NULL,
                vector      BLOB NOT NULL,
                metadata    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_emb_source ON embeddings(source);
        """)

    def _get_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._model = SentenceTransformer("BAAI/bge-small-en-v1.5")
            return self._model
        except ImportError:
            logger.warning("sentence-transformers not installed — semantic search disabled")
            return None

    def _encode_vector(self, text: str) -> np.ndarray | None:
        model = self._get_model()
        if model is None:
            return None
        vec = model.encode(text, normalize_embeddings=True)
        return np.asarray(vec, dtype=np.float32)

    def encode(self, text: str) -> bytes | None:
        vec = self._encode_vector(text)
        return None if vec is None else _pack(vec)

    # ------------------------------------------------------------------
    # In-RAM matrix maintenance
    # ------------------------------------------------------------------

    def _ensure_matrix_loaded(self) -> None:
        if self._matrix_loaded:
            return
        rows = self.db.execute("SELECT id, source, text, vector, metadata FROM embeddings").fetchall()
        ids, sources, texts, metas, vectors = [], [], [], [], []
        for row in rows:
            try:
                vectors.append(_unpack(row["vector"]))
            except struct.error:
                continue  # corrupt/truncated vector — skip rather than crash the whole load
            ids.append(row["id"])
            sources.append(row["source"])
            texts.append(row["text"])
            metas.append(json.loads(row["metadata"]) if row["metadata"] else {})
        self._ids = ids
        self._sources = sources
        self._texts = texts
        self._metadata = metas
        self._matrix = np.vstack(vectors) if vectors else np.zeros((0, DIMS), dtype=np.float32)
        self._matrix_loaded = True

    def _matrix_upsert(self, id: str, source: str, text: str, metadata: dict, vec: np.ndarray) -> None:
        self._ensure_matrix_loaded()
        self._matrix_remove(id)  # INSERT OR REPLACE semantics — drop any prior row for this id first
        self._ids.append(id)
        self._sources.append(source)
        self._texts.append(text)
        self._metadata.append(metadata)
        self._matrix = np.vstack([self._matrix, vec.reshape(1, -1)])

    def _matrix_remove(self, id: str) -> None:
        if not self._matrix_loaded:
            return
        try:
            idx = self._ids.index(id)
        except ValueError:
            return
        del self._ids[idx]
        del self._sources[idx]
        del self._texts[idx]
        del self._metadata[idx]
        self._matrix = np.delete(self._matrix, idx, axis=0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, id: str, source: str, text: str, metadata: dict[str, Any] | None = None) -> bool:
        vec = self._encode_vector(text)
        if vec is None:
            return False
        meta = metadata or {}
        self.db.execute(
            "INSERT OR REPLACE INTO embeddings (id, source, text, vector, metadata) VALUES (?, ?, ?, ?, ?)",
            (id, source, text, _pack(vec), json.dumps(meta)),
        )
        self.db.commit()
        self._matrix_upsert(id, source, text, meta, vec)
        return True

    def search(self, query: str, source: str | None = None, limit: int = 10) -> list[dict]:
        q_vec = self._encode_vector(query)
        if q_vec is None:
            return []
        return self.search_by_vector(q_vec, source=source, limit=limit)

    def search_by_vector(self, q_vec: np.ndarray, source: str | None = None, limit: int = 10) -> list[dict]:
        """Same as search(), but takes an already-encoded query vector.

        Split out so the matrix search itself — the part Step E's NumPy
        rewrite is actually about — can be measured/benchmarked independent
        of model-inference latency (a separate, already-existing cost this
        change doesn't touch), and so a caller that already has a vector
        (e.g. a future rerank step) doesn't pay to re-encode.
        """
        self._ensure_matrix_loaded()
        if self._matrix.shape[0] == 0:
            return []

        # Vectors are L2-normalized at encode time, so one matmul gives cosine
        # similarity directly — no per-row Python loop, no per-row sqrt.
        # Avoid the fancy-index copy entirely in the common unfiltered case —
        # at 50k rows that copy (~76MB) would cost more than the matmul itself.
        if source is not None:
            idxs = np.array([i for i, s in enumerate(self._sources) if s == source], dtype=np.int64)
            if idxs.size == 0:
                return []
            scores = self._matrix[idxs] @ q_vec
        else:
            idxs = None
            scores = self._matrix @ q_vec

        k = min(limit, scores.shape[0])
        if k <= 0:
            return []
        top_local = np.argpartition(-scores, k - 1)[:k]
        top_local = top_local[np.argsort(-scores[top_local])]

        results = []
        for local_i in top_local:
            global_i = int(idxs[local_i]) if idxs is not None else int(local_i)
            results.append({
                "id": self._ids[global_i],
                "source": self._sources[global_i],
                "text": self._texts[global_i],
                "metadata": self._metadata[global_i],
                "score": float(scores[local_i]),
            })
        return results

    def delete(self, id: str) -> None:
        self.db.execute("DELETE FROM embeddings WHERE id = ?", (id,))
        self.db.commit()
        self._matrix_remove(id)
