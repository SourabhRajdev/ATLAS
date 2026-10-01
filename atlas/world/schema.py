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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    key             TEXT NOT NULL,
    value           TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 1.0,
    source          TEXT NOT NULL,
    recorded_at     REAL NOT NULL,
    superseded_by   INTEGER REFERENCES attributes(id),
    taint           TEXT NOT NULL DEFAULT 'clean',
    evidence_msg_id TEXT,
    valid_to        REAL
);

-- idx_attr_entity and idx_attr_current are created in _migrate_attributes_schema
-- below, not here. A fresh DB's CREATE TABLE above already has `valid_to`, but
-- an EXISTING pre-migration DB's attributes table doesn't yet — CREATE TABLE
-- IF NOT EXISTS is a no-op against it, so an index referencing `valid_to` right
-- here would fail on that column not existing yet. _migrate_attributes_schema
-- adds the column (or rebuilds the table) first, then creates both indexes,
-- so it works for both a fresh DB and an existing one.

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
    _migrate_attributes_schema(conn)
    return conn


def _migrate_attributes_schema(conn: sqlite3.Connection) -> None:
    """Additive migration for DBs created before taint/evidence/valid_to and
    before the UNIQUE(entity_id, key, source) -> partial-index fix.

    A fresh DB already gets the new DDL above, so all of this is a no-op on
    it; it only does real work against a pre-existing world.db.
    """
    for col, defn in [
        ("taint", "TEXT NOT NULL DEFAULT 'clean'"),
        ("evidence_msg_id", "TEXT"),
        ("valid_to", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE attributes ADD COLUMN {col} {defn}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Detect the old table-level UNIQUE(entity_id, key, source): SQLite
    # backs it with an auto-named unique index. If present, it must be
    # removed by rebuilding the table — SQLite can't drop a constraint via
    # ALTER TABLE, and leaving it in place keeps the delete-on-insert bug
    # alive regardless of what application code does.
    index_rows = conn.execute("PRAGMA index_list(attributes)").fetchall()
    has_old_unique = any(
        row["unique"] and row["origin"] == "u" and "autoindex" in row["name"]
        for row in index_rows
    )
    if has_old_unique:
        conn.executescript("""
            CREATE TABLE attributes_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                key             TEXT NOT NULL,
                value           TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 1.0,
                source          TEXT NOT NULL,
                recorded_at     REAL NOT NULL,
                superseded_by   INTEGER REFERENCES attributes_new(id),
                taint           TEXT NOT NULL DEFAULT 'clean',
                evidence_msg_id TEXT,
                valid_to        REAL
            );
            INSERT INTO attributes_new
                (id, entity_id, key, value, confidence, source, recorded_at,
                 superseded_by, taint, evidence_msg_id, valid_to)
            SELECT id, entity_id, key, value, confidence, source, recorded_at,
                   superseded_by, taint, evidence_msg_id, valid_to
            FROM attributes;
            DROP TABLE attributes;
            ALTER TABLE attributes_new RENAME TO attributes;
        """)

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_attr_current "
        "ON attributes(entity_id, key, source) WHERE valid_to IS NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attr_entity ON attributes(entity_id, key)")
    conn.commit()
