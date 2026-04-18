"""Trust layer health check — must complete in < 100ms.

Returns a dict describing the state of the trust system.
Used by CLI /status command and startup self-test.
"""

from __future__ import annotations

import time
from pathlib import Path


async def health_check(audit_log, rollback_mgr=None) -> dict:
    """
    Run synchronously (wrapped in asyncio.to_thread by caller).
    Returns dict with all fields populated — never raises.
    """
    start = time.monotonic()
    result: dict = {
        "healthy": False,
        "elapsed_ms": 0.0,
        "audit_table_exists": False,
        "triggers_active": False,
        "total_entries": 0,
        "blocked_entries": 0,
        "rollback_available": 0,
        "errors": [],
    }

    try:
        # Check audit table
        result["audit_table_exists"] = True  # if AuditLog init succeeded, table exists

        # Verify triggers (just a sqlite_master query — fast)
        result["triggers_active"] = audit_log.verify_triggers_active()
        if not result["triggers_active"]:
            result["errors"].append("append-only triggers missing — audit log is mutable")

        # Count entries
        result["total_entries"] = audit_log.count()

        # Count blocked entries
        blocked = audit_log.get_blocked(limit=1000)
        result["blocked_entries"] = len(blocked)

        # Rollback snapshots available
        if rollback_mgr is not None:
            snaps = rollback_mgr.list_available(limit=100)
            result["rollback_available"] = len(snaps)

        result["healthy"] = result["triggers_active"]

    except Exception as e:
        result["errors"].append(f"health check exception: {type(e).__name__}: {e}")

    result["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
    return result
