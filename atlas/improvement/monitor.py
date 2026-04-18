"""BehaviorMonitor — collects quality signals during normal operation.

Called from executor/orchestrator hooks. Writes signals to SQLite immediately.
Pattern detection runs asynchronously during weekly report generation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from atlas.improvement.models import ImpactLevel, QualitySignal, SignalKind

logger = logging.getLogger("atlas.improvement.monitor")

_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS quality_signals (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    impact      TEXT NOT NULL,
    context     TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    value       REAL NOT NULL DEFAULT 0.0,
    session_id  TEXT NOT NULL DEFAULT '',
    recorded_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_kind ON quality_signals(kind, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_impact ON quality_signals(impact, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_session ON quality_signals(session_id);

CREATE TABLE IF NOT EXISTS behavior_reports (
    week_key     TEXT PRIMARY KEY,
    generated_at REAL NOT NULL,
    payload      TEXT NOT NULL   -- JSON of WeeklyReport
);
"""

_POSITIVE_WORDS = frozenset({
    "good", "great", "perfect", "excellent", "thanks", "thank you",
    "yes", "correct", "right", "exactly", "nice", "well done",
})
_NEGATIVE_WORDS = frozenset({
    "wrong", "no", "incorrect", "bad", "stop", "don't", "not what",
    "that's not", "you missed", "error", "mistake", "again",
})


def classify_user_message(text: str) -> ImpactLevel | None:
    """Quick heuristic: is this user feedback positive or negative?"""
    lower = text.lower()
    pos = sum(1 for w in _POSITIVE_WORDS if w in lower)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in lower)
    if pos > neg:
        return ImpactLevel.POSITIVE
    if neg > pos:
        return ImpactLevel.NEGATIVE
    return None


class BehaviorMonitor:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._session_id = str(int(time.time()))

    def record(self, signal: QualitySignal) -> None:
        signal.session_id = self._session_id
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO quality_signals "
                "(id, kind, impact, context, detail, value, session_id, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (signal.id, signal.kind.value, signal.impact.value,
                 signal.context, signal.detail, signal.value,
                 signal.session_id, signal.recorded_at),
            )
            self._conn.commit()
        except Exception as e:
            logger.error("Failed to record signal: %s", e)

    def record_task_timing(
        self,
        task_name: str,
        estimated_minutes: int,
        actual_minutes: int,
    ) -> None:
        if estimated_minutes <= 0:
            return
        ratio = actual_minutes / estimated_minutes
        if ratio >= 2.0:
            sig = QualitySignal.create(
                kind=SignalKind.TASK_DURATION_OVERRUN,
                impact=ImpactLevel.NEGATIVE,
                context=task_name,
                detail=f"estimated={estimated_minutes}min actual={actual_minutes}min",
                value=ratio,
            )
            self.record(sig)

    def record_tool_error(self, tool_name: str, error: str) -> None:
        sig = QualitySignal.create(
            kind=SignalKind.TOOL_ERROR_SPIKE,
            impact=ImpactLevel.NEGATIVE,
            context=tool_name,
            detail=error[:200],
        )
        self.record(sig)

    def record_user_feedback(self, message: str, context: str = "") -> None:
        impact = classify_user_message(message)
        if impact is None:
            return
        kind = SignalKind.POSITIVE_FEEDBACK if impact == ImpactLevel.POSITIVE else SignalKind.NEGATIVE_FEEDBACK
        sig = QualitySignal.create(
            kind=kind,
            impact=impact,
            context=context or "user_message",
            detail=message[:200],
        )
        self.record(sig)

    def record_user_correction(self, context: str, detail: str = "") -> None:
        sig = QualitySignal.create(
            kind=SignalKind.USER_CORRECTION,
            impact=ImpactLevel.NEGATIVE,
            context=context,
            detail=detail[:200],
        )
        self.record(sig)

    def record_goal_event(self, goal_title: str, completed: bool) -> None:
        kind = SignalKind.GOAL_COMPLETED if completed else SignalKind.GOAL_ABANDONED
        impact = ImpactLevel.POSITIVE if completed else ImpactLevel.NEGATIVE
        sig = QualitySignal.create(
            kind=kind,
            impact=impact,
            context=goal_title,
        )
        self.record(sig)

    def get_recent_signals(
        self,
        days: int = 7,
        kind: SignalKind | None = None,
    ) -> list[QualitySignal]:
        cutoff = time.time() - days * 86400
        if kind:
            rows = self._conn.execute(
                "SELECT * FROM quality_signals WHERE kind=? AND recorded_at>? ORDER BY recorded_at DESC",
                (kind.value, cutoff),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM quality_signals WHERE recorded_at>? ORDER BY recorded_at DESC",
                (cutoff,),
            ).fetchall()
        return [
            QualitySignal(
                id=r["id"], kind=SignalKind(r["kind"]), impact=ImpactLevel(r["impact"]),
                context=r["context"], detail=r["detail"], value=r["value"],
                session_id=r["session_id"], recorded_at=r["recorded_at"],
            )
            for r in rows
        ]

    def get_signal_counts(self, days: int = 7) -> dict[str, int]:
        cutoff = time.time() - days * 86400
        rows = self._conn.execute(
            "SELECT kind, COUNT(*) as n FROM quality_signals "
            "WHERE recorded_at > ? GROUP BY kind",
            (cutoff,),
        ).fetchall()
        return {r["kind"]: r["n"] for r in rows}

    def save_report(self, week_key: str, report_json: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO behavior_reports (week_key, generated_at, payload) "
            "VALUES (?, ?, ?)",
            (week_key, time.time(), report_json),
        )
        self._conn.commit()

    def get_report(self, week_key: str) -> str | None:
        row = self._conn.execute(
            "SELECT payload FROM behavior_reports WHERE week_key=?", (week_key,)
        ).fetchone()
        return row["payload"] if row else None

    def close(self) -> None:
        self._conn.close()
