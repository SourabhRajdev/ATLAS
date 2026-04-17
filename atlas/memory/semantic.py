"""Semantic memory — local embeddings for vector search.

Uses sentence-transformers/bge-small-en-v1.5 (33M params, ~60MB). Vectors
stored in SQLite as blobs. No external service needed.

The model loads lazily on first encode(). On M-series ~15ms per embed.
"""

from __future__ import annotations

import json
import logging
import struct
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("atlas.memory.semantic")

DIMS = 384  # bge-small output


class SemanticStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self._model = None
        self._init_tables()

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

    def encode(self, text: str) -> bytes | None:
        model = self._get_model()
        if model is None:
            return None
        vec = model.encode(text, normalize_embeddings=True)
        return struct.pack(f"{DIMS}f", *vec.tolist())

    def add(self, id: str, source: str, text: str, metadata: dict[str, Any] | None = None) -> bool:
        blob = self.encode(text)
        if blob is None:
            return False
        self.db.execute(
            "INSERT OR REPLACE INTO embeddings (id, source, text, vector, metadata) VALUES (?, ?, ?, ?, ?)",
            (id, source, text, blob, json.dumps(metadata or {})),
        )
        self.db.commit()
        return True

    def search(self, query: str, source: str | None = None, limit: int = 10) -> list[dict]:
        q_blob = self.encode(query)
        if q_blob is None:
            return []
        q_vec = struct.unpack(f"{DIMS}f", q_blob)
        clause = "WHERE source = ?" if source else ""
        params: tuple = (source,) if source else ()
        rows = self.db.execute(
            f"SELECT id, source, text, vector, metadata FROM embeddings {clause}",
            params,
        ).fetchall()
        scored = []
        for r in rows:
            r_vec = struct.unpack(f"{DIMS}f", r["vector"])
            sim = _cosine(q_vec, r_vec)
            scored.append({
                "id": r["id"],
                "source": r["source"],
                "text": r["text"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "score": sim,
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def delete(self, id: str) -> None:
        self.db.execute("DELETE FROM embeddings WHERE id = ?", (id,))
        self.db.commit()


def _cosine(a: tuple, b: tuple) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    # Vectors are L2-normalized so dot product == cosine similarity
    return dot
