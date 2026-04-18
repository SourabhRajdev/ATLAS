"""SQLite schema for long-horizon planning."""

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS goals (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',
    priority        TEXT NOT NULL DEFAULT 'medium',
    due_date        REAL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    success_criteria TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',   -- JSON array
    progress        REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_priority ON goals(priority, due_date);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    goal_id             TEXT NOT NULL REFERENCES goals(id),
    title               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending',
    priority            TEXT NOT NULL DEFAULT 'medium',
    estimated_minutes   INTEGER NOT NULL DEFAULT 30,
    actual_minutes      INTEGER NOT NULL DEFAULT 0,
    due_date            REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    completed_at        REAL,
    depends_on          TEXT NOT NULL DEFAULT '[]',  -- JSON array of task IDs
    suggested_action    TEXT NOT NULL DEFAULT '',
    week_number         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_week ON tasks(week_number, status);

CREATE TABLE IF NOT EXISTS week_plans (
    week_key        TEXT PRIMARY KEY,   -- "YYYY-WNN"
    week_number     INTEGER NOT NULL,
    year            INTEGER NOT NULL,
    goal_ids        TEXT NOT NULL DEFAULT '[]',
    task_ids        TEXT NOT NULL DEFAULT '[]',
    capacity_minutes INTEGER NOT NULL DEFAULT 600,
    notes           TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS planning_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,   -- "goal_created" | "task_completed" | "replanned" | ...
    entity_id   TEXT,
    payload     TEXT,            -- JSON
    recorded_at REAL NOT NULL
);
"""
