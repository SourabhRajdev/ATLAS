"""Implicit feedback learning — adjusts signal priority weights over time.

No user labeling required. Outcomes are inferred from behavior:
- User acted immediately → signal was valuable (boost +0.1)
- User dismissed → signal was unwanted (boost -0.15)
- User ignored for 5 minutes → treated as dismissed

After 20 samples of the same signal type → adjust the base weight.
Changes are stored in SQLite, not in memory, so they survive restarts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from atlas.proactive.signals import Signal

logger = logging.getLogger("atlas.proactive.learning")

_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS signal_outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_type TEXT NOT NULL,
    signal_id   TEXT NOT NULL,
    outcome     TEXT NOT NULL,    -- "acted"|"dismissed"|"ignored"
    priority_at REAL NOT NULL,    -- priority when surfaced
    ts          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outcomes_type ON signal_outcomes(signal_type, ts DESC);

CREATE TABLE IF NOT EXISTS priority_weights (
    signal_type   TEXT PRIMARY KEY,
    weight_delta  REAL NOT NULL DEFAULT 0.0,  -- cumulative learned adjustment
    sample_count  INTEGER NOT NULL DEFAULT 0,
    last_updated  REAL NOT NULL
);
"""

SAMPLE_THRESHOLD = 20        # samples needed before adjusting weights
ACT_BOOST = 0.1
DISMISS_PENALTY = -0.15
IGNORE_PENALTY = -0.15       # same as dismiss
MAX_WEIGHT_DELTA = 0.4       # cap cumulative adjustment at ±0.4


class FeedbackLearner:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record_outcome(self, signal: Signal, outcome: str) -> None:
        """Record the outcome of a surfaced signal."""
        if outcome not in ("acted", "dismissed", "ignored"):
            logger.warning("Unknown outcome: %s", outcome)
            return

        self._conn.execute(
            "INSERT INTO signal_outcomes (signal_type, signal_id, outcome, priority_at, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (signal.type, signal.id, outcome, signal.effective_priority(), time.time()),
        )
        self._conn.commit()
        self._maybe_update_weight(signal.type)

    def get_weight_delta(self, signal_type: str) -> float:
        """Return the learned priority delta for this signal type."""
        row = self._conn.execute(
            "SELECT weight_delta FROM priority_weights WHERE signal_type = ?",
            (signal_type,),
        ).fetchone()
        return row["weight_delta"] if row else 0.0

    def apply_learned_weights(self, signal: Signal) -> Signal:
        """Apply learned weight delta to signal's priority_boost."""
        delta = self.get_weight_delta(signal.type)
        signal.priority_boost += delta
        return signal

    def _maybe_update_weight(self, signal_type: str) -> None:
        """After SAMPLE_THRESHOLD samples, recalculate and store weight delta."""
        rows = self._conn.execute(
            "SELECT outcome FROM signal_outcomes WHERE signal_type = ? ORDER BY ts DESC LIMIT ?",
            (signal_type, SAMPLE_THRESHOLD),
        ).fetchall()

        if len(rows) < SAMPLE_THRESHOLD:
            return

        # Calculate net delta from recent samples
        net = 0.0
        for row in rows:
            if row["outcome"] == "acted":
                net += ACT_BOOST
            elif row["outcome"] in ("dismissed", "ignored"):
                net += DISMISS_PENALTY

        # Normalize to per-signal delta
        avg_delta = net / len(rows)
        clamped = max(-MAX_WEIGHT_DELTA, min(MAX_WEIGHT_DELTA, avg_delta * len(rows)))

        existing = self._conn.execute(
            "SELECT weight_delta, sample_count FROM priority_weights WHERE signal_type = ?",
            (signal_type,),
        ).fetchone()

        if existing:
            new_delta = max(-MAX_WEIGHT_DELTA, min(MAX_WEIGHT_DELTA,
                            existing["weight_delta"] * 0.7 + clamped * 0.3))  # EMA
            new_count = existing["sample_count"] + len(rows)
            self._conn.execute(
                "UPDATE priority_weights SET weight_delta = ?, sample_count = ?, last_updated = ? "
                "WHERE signal_type = ?",
                (new_delta, new_count, time.time(), signal_type),
            )
        else:
            self._conn.execute(
                "INSERT INTO priority_weights (signal_type, weight_delta, sample_count, last_updated) "
                "VALUES (?, ?, ?, ?)",
                (signal_type, clamped, len(rows), time.time()),
            )
        self._conn.commit()
        logger.info("Updated priority weight for %s: delta=%.3f", signal_type, clamped)

    def get_stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT signal_type, weight_delta, sample_count FROM priority_weights "
            "ORDER BY ABS(weight_delta) DESC"
        ).fetchall()
        return {
            "learned_weights": [
                {"type": r["signal_type"], "delta": r["weight_delta"], "samples": r["sample_count"]}
                for r in rows
            ]
        }

    def close(self) -> None:
        self._conn.close()
