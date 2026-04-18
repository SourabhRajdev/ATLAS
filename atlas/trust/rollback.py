"""Rollback manager — snapshot reversible actions before execution.

Only write_file and set_clipboard are snapshotted because:
- write_file: we can read the old content before overwriting
- set_clipboard: we can read the current clipboard value

Other HIGH-consequence tools (delete_file) go through CONFIRM tier, so the
user is already the last line of defense. Snapshot-based rollback is for
mistakes, not security — it's a safety net for approved actions.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

logger = logging.getLogger("atlas.trust.rollback")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rollback_snapshots (
    id          TEXT PRIMARY KEY,
    ts          REAL NOT NULL,
    tool_name   TEXT NOT NULL,
    params_json TEXT NOT NULL,
    snapshot    TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snap_ts ON rollback_snapshots(ts DESC);
"""

# Snapshots older than this are auto-purged (keep last 24h)
_MAX_AGE_SECONDS = 86_400
# Keep at most this many snapshots
_MAX_COUNT = 200


class RollbackManager:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def snapshot(self, tool_name: str, params: dict) -> str | None:
        """Capture pre-execution state. Returns snapshot_id or None if not applicable."""
        snap = self._capture(tool_name, params)
        if snap is None:
            return None

        snap_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO rollback_snapshots (id, ts, tool_name, params_json, snapshot) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap_id, time.time(), tool_name,
             json.dumps(params, default=str)[:2000], snap),
        )
        self._conn.commit()
        self._purge_old()
        return snap_id

    def rollback(self, snapshot_id: str) -> tuple[bool, str]:
        """Apply snapshot. Returns (success, message)."""
        row = self._conn.execute(
            "SELECT * FROM rollback_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()

        if not row:
            return False, f"snapshot {snapshot_id} not found"
        if row["used"]:
            return False, f"snapshot {snapshot_id} already applied"

        tool = row["tool_name"]
        params = json.loads(row["params_json"])
        snap = row["snapshot"]

        ok, msg = self._apply(tool, params, snap)
        if ok:
            # Mark used — snapshot table does NOT have append-only triggers,
            # because rollback naturally needs to mark entries as consumed.
            self._conn.execute(
                "UPDATE rollback_snapshots SET used = 1 WHERE id = ?", (snapshot_id,)
            )
            self._conn.commit()

        return ok, msg

    def list_available(self, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, ts, tool_name, params_json FROM rollback_snapshots "
            "WHERE used = 0 ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal: capture + apply per tool
    # ------------------------------------------------------------------

    def _capture(self, tool_name: str, params: dict) -> str | None:
        if tool_name == "write_file":
            path_str = params.get("path", "")
            if not path_str:
                return None
            path = Path(path_str).expanduser()
            if path.exists() and path.is_file():
                try:
                    return path.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    logger.warning("snapshot read failed: %s", e)
                    return None
            return "__did_not_exist__"

        if tool_name == "set_clipboard":
            try:
                import subprocess
                result = subprocess.run(
                    ["pbpaste"], capture_output=True, text=True, timeout=2
                )
                return result.stdout
            except Exception:
                return None

        return None

    def _apply(self, tool_name: str, params: dict, snap: str) -> tuple[bool, str]:
        if tool_name == "write_file":
            path_str = params.get("path", "")
            if not path_str:
                return False, "no path in snapshot params"
            path = Path(path_str).expanduser()
            if snap == "__did_not_exist__":
                try:
                    path.unlink(missing_ok=True)
                    return True, f"deleted {path} (file did not exist before)"
                except OSError as e:
                    return False, f"delete failed: {e}"
            try:
                path.write_text(snap, encoding="utf-8")
                return True, f"restored {path} ({len(snap)} chars)"
            except OSError as e:
                return False, f"write failed: {e}"

        if tool_name == "set_clipboard":
            try:
                import subprocess
                subprocess.run(
                    ["pbcopy"], input=snap, text=True, timeout=2, check=True
                )
                return True, "clipboard restored"
            except Exception as e:
                return False, f"pbcopy failed: {e}"

        return False, f"no rollback handler for {tool_name}"

    def _purge_old(self) -> None:
        cutoff = time.time() - _MAX_AGE_SECONDS
        self._conn.execute(
            "DELETE FROM rollback_snapshots WHERE ts < ?", (cutoff,)
        )
        # Also trim to max count
        self._conn.execute(
            """DELETE FROM rollback_snapshots WHERE id NOT IN (
               SELECT id FROM rollback_snapshots ORDER BY ts DESC LIMIT ?
            )""",
            (_MAX_COUNT,),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
