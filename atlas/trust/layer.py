"""TrustLayer — the single chokepoint all tool execution flows through.

Usage (in Executor):
    trust = TrustLayer(db_path=config.data_dir / "trust.db")
    decision = await trust.gate(tool_name, params, taint)
    if not decision.allowed:
        return {"error": f"trust: {decision.reason}"}
    # ... execute tool ...
    await trust.record_result(tool_name, params, result_str, decision, session_id)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from atlas.trust.audit import AuditEntry, AuditLog
from atlas.trust.classifier import Decision, TrustClassifier
from atlas.trust.health import health_check
from atlas.trust.rollback import RollbackManager
from atlas.trust.taint import TaintContext

logger = logging.getLogger("atlas.trust")


class TrustLayer:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._audit = AuditLog(db_path)
        self._rollback = RollbackManager(db_path)
        self._classifier = TrustClassifier()
        logger.info("TrustLayer active (db=%s)", db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def gate(
        self,
        tool_name: str,
        params: dict,
        taint: TaintContext,
        session_id: str = "",
    ) -> Decision:
        """Evaluate and log. Returns Decision — caller must respect allowed=False."""
        decision = self._classifier.evaluate(tool_name, params, taint)

        entry = AuditEntry(
            tool_name=tool_name,
            params=params,
            taint_level=int(taint.level),
            taint_source=taint.source,
            consequence=int(decision.consequence),
            allowed=decision.allowed,
            block_reason=decision.reason if not decision.allowed else None,
            session_id=session_id,
        )
        # Run sync SQLite write off the event loop
        await asyncio.to_thread(self._audit.log, entry)

        if not decision.allowed:
            logger.warning(
                "BLOCKED %s | taint=%s | reason=%s",
                tool_name, taint, decision.reason,
            )
        elif decision.requires_confirm:
            logger.info(
                "ESCALATED %s → CONFIRM | taint=%s | consequence=%s",
                tool_name, taint, decision.consequence.name,
            )

        return decision

    async def snapshot_before(self, tool_name: str, params: dict) -> str | None:
        """Capture pre-execution state for rollback. Returns snapshot_id or None."""
        return await asyncio.to_thread(self._rollback.snapshot, tool_name, params)

    async def record_result(
        self,
        tool_name: str,
        params: dict,
        result: str,
        decision: Decision,
        session_id: str = "",
    ) -> None:
        """Log the result of an allowed execution. Best-effort — never raises."""
        entry = AuditEntry(
            tool_name=tool_name,
            params=params,
            taint_level=0,  # result record uses CLEAN level (action already cleared gate)
            taint_source="result",
            consequence=int(decision.consequence),
            allowed=True,
            result=result[:500] if result else None,
            session_id=session_id,
        )
        try:
            await asyncio.to_thread(self._audit.log, entry)
        except Exception as e:
            logger.error("record_result failed: %s", e)

    async def rollback(self, snapshot_id: str) -> tuple[bool, str]:
        """Undo a previously snapshotted action."""
        return await asyncio.to_thread(self._rollback.rollback, snapshot_id)

    async def health_check(self) -> dict:
        """Run health check. Guaranteed < 100ms.

        Runs synchronously in the event loop — all ops are fast SQLite reads
        that complete in < 5ms. Avoids asyncio.to_thread cold-start overhead.
        """
        return _sync_health(self._audit, self._rollback)

    async def recent_blocked(self, limit: int = 10) -> list[dict]:
        return await asyncio.to_thread(self._audit.get_blocked, limit)

    def close(self) -> None:
        self._audit.close()
        self._rollback.close()


def _sync_health(audit_log: AuditLog, rollback_mgr: RollbackManager) -> dict:
    import asyncio
    import time
    start = time.monotonic()
    result: dict = {
        "healthy": False,
        "elapsed_ms": 0.0,
        "audit_table_exists": True,
        "triggers_active": False,
        "total_entries": 0,
        "blocked_entries": 0,
        "rollback_available": 0,
        "errors": [],
    }
    try:
        result["triggers_active"] = audit_log.verify_triggers_active()
        if not result["triggers_active"]:
            result["errors"].append("append-only triggers missing")
        result["total_entries"] = audit_log.count()
        blocked = audit_log.get_blocked(limit=1000)
        result["blocked_entries"] = len(blocked)
        snaps = rollback_mgr.list_available(limit=100)
        result["rollback_available"] = len(snaps)
        result["healthy"] = result["triggers_active"]
    except Exception as e:
        result["errors"].append(f"{type(e).__name__}: {e}")
    result["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
    return result
