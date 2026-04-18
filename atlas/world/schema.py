"""SQLite DDL for the World Model database.

Each system gets its own .db file. This module owns world.db exclusively.
Does NOT touch atlas.db or trust.db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS entities (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    name             TEXT NOT NULL,
    canonical_name   TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 1.0,
    first_seen       REAL NOT NULL,
    last_updated     REAL NOT NULL,
    last_reinforced  REAL NOT NULL,
    source           TEXT NOT NULL,
    metadata         TEXT NOT NULL DEFAULT '{}',
    embedding        BLOB
);

CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical_name);
CREATE INDEX IF NOT EXISTS idx_entities_type      ON entities(type, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_entities_updated   ON entities(last_updated DESC);

CREATE TABLE IF NOT EXISTS attributes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id      TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    key            TEXT NOT NULL,
    value          TEXT NOT NULL,
    confidence     REAL NOT NULL DEFAULT 1.0,
    source         TEXT NOT NULL,
    recorded_at    REAL NOT NULL,
    superseded_by  INTEGER REFERENCES attributes(id),
    UNIQUE(entity_id, key, source)
);

CREATE INDEX IF NOT EXISTS idx_attr_entity ON attributes(entity_id, key);

CREATE TABLE IF NOT EXISTS relationships (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_entity     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    strength      REAL NOT NULL DEFAULT 0.5,
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL,
    source        TEXT NOT NULL,
    UNIQUE(from_entity, to_entity, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships(from_entity);
CREATE INDEX IF NOT EXISTS idx_rel_to   ON relationships(to_entity);

CREATE TABLE IF NOT EXISTS world_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type        TEXT NOT NULL,
    source            TEXT NOT NULL,
    payload           TEXT NOT NULL,
    processed         INTEGER NOT NULL DEFAULT 0,
    processed_at      REAL,
    entities_affected TEXT NOT NULL DEFAULT '[]',
    recorded_at       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_unprocessed ON world_events(processed, recorded_at);
CREATE INDEX IF NOT EXISTS idx_events_type        ON world_events(event_type, recorded_at DESC);
"""


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn
