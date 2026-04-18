"""iMessage integration — read-only access to local chat.db.

Opens the SQLite database in WAL mode with immutable=1 to avoid corrupting
the live Messages.app database. Only reads messages newer than last poll.

Privacy rule: iMessage content NEVER leaves the device. This integration
only returns messages to in-process consumers — never to cloud LLMs directly.
All callers must check atlas.trust.taint before forwarding.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from atlas.integrations.base import BaseIntegration, IntegrationHealth

logger = logging.getLogger("atlas.integrations.imessage")

DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

# Apple epoch: seconds since 2001-01-01. Convert to Unix by adding offset.
APPLE_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01
APPLE_NANOSECOND_DIVISOR = 1_000_000_000  # pre-BigSur timestamps are in nanoseconds

_CONTACT_QUERY = """
SELECT
    m.rowid,
    m.guid,
    m.text,
    m.date,
    m.is_from_me,
    m.cache_roomnames,
    COALESCE(h.id, '') AS handle_id
FROM message m
LEFT JOIN handle h ON m.handle_id = h.rowid
WHERE m.date > ?
  AND m.text IS NOT NULL
  AND m.text != ''
ORDER BY m.date ASC
LIMIT 200
"""


def _apple_ts_to_unix(apple_ts: int) -> float:
    """Convert Apple CoreData timestamp to Unix seconds.

    Pre-BigSur chat.db stores nanoseconds since Apple epoch (2001-01-01).
    BigSur+ stores seconds since Apple epoch. Threshold: values > 1e12 are ns.
    """
    if apple_ts > 1_000_000_000_000:  # nanoseconds threshold (safely > any realistic second value)
        return apple_ts / APPLE_NANOSECOND_DIVISOR + APPLE_EPOCH_OFFSET
    return apple_ts + APPLE_EPOCH_OFFSET


def _unix_to_apple_ts(unix_ts: float) -> int:
    """Convert Unix timestamp to Apple nanosecond format (pre-BigSur compatible)."""
    return int((unix_ts - APPLE_EPOCH_OFFSET) * APPLE_NANOSECOND_DIVISOR)


class IMessageIntegration(BaseIntegration):
    name = "imessage"

    def __init__(
        self,
        chat_db_path: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self._chat_db = chat_db_path or DEFAULT_CHAT_DB
        self._cursor_path = (data_dir / "imessage_cursor.txt") if data_dir else None
        self._last_apple_ts: int = self._load_cursor()

    def _load_cursor(self) -> int:
        if self._cursor_path and self._cursor_path.exists():
            try:
                return int(self._cursor_path.read_text().strip())
            except Exception:
                pass
        # Default: messages from last 24 hours
        unix_24h_ago = time.time() - 86400
        return _unix_to_apple_ts(unix_24h_ago)

    def _save_cursor(self, ts: int) -> None:
        self._last_apple_ts = ts
        if self._cursor_path:
            self._cursor_path.write_text(str(ts))

    def _open_db(self) -> sqlite3.Connection:
        """Open chat.db read-only with WAL to avoid blocking Messages.app."""
        uri = f"file:{self._chat_db}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    async def poll(self) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._poll_sync)

    def _poll_sync(self) -> list[dict]:
        if not self._chat_db.exists():
            self._fail(f"chat.db not found at {self._chat_db}")
            return []

        events: list[dict] = []
        max_ts = self._last_apple_ts

        try:
            conn = self._open_db()
            try:
                rows = conn.execute(_CONTACT_QUERY, (self._last_apple_ts,)).fetchall()
                for row in rows:
                    apple_ts = row["date"]
                    unix_ts = _apple_ts_to_unix(apple_ts)
                    events.append({
                        "type": "imessage_received",
                        "source": "imessage",
                        "id": row["guid"],
                        "text": row["text"],
                        "from_me": bool(row["is_from_me"]),
                        "handle": row["handle_id"],
                        "group": row["cache_roomnames"] or "",
                        "timestamp": unix_ts,
                        "_local_only": True,  # privacy marker
                    })
                    if apple_ts > max_ts:
                        max_ts = apple_ts
            finally:
                conn.close()

            if max_ts > self._last_apple_ts:
                self._save_cursor(max_ts)

            self._ok({"messages_fetched": len(events)})
        except sqlite3.OperationalError as e:
            if "disk I/O error" in str(e) or "unable to open" in str(e).lower():
                self._fail(f"Cannot access chat.db — grant Full Disk Access in Privacy settings: {e}")
            else:
                self._fail(str(e))
            logger.error("iMessage poll error: %s", e)
        except Exception as e:
            self._fail(str(e))
            logger.error("iMessage poll error: %s", e)

        return events

    def health_check(self) -> IntegrationHealth:
        if not self._chat_db.exists():
            self._health.status = "down"
            self._health.error = f"chat.db not found: {self._chat_db}"
        return self._health
