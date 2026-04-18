"""Calendar integration — polls Apple Calendar via AppleScript.

Wraps atlas.control.applescript.AppleScriptBackend and adds:
- Proper ISO date parsing from AppleScript output
- Meeting-now / meeting-soon detection
- Structured event dicts for proactive signals
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from atlas.control.applescript import AppleScriptBackend
from atlas.control.models import Action
from atlas.integrations.base import BaseIntegration, IntegrationHealth

logger = logging.getLogger("atlas.integrations.calendar")

# AppleScript returns dates like: "Friday, April 18, 2026 at 10:00:00 AM"
# or locale-dependent variants — we try multiple patterns.
_DATE_PATTERNS = [
    # "Friday, April 18, 2026 at 10:00:00 AM"
    (r"\w+, (\w+ \d+, \d{4}) at (\d+:\d+:\d+ [AP]M)", "%B %d, %Y %I:%M:%S %p"),
    # "04/18/2026, 10:00 AM"
    (r"(\d{2}/\d{2}/\d{4}), (\d+:\d+ [AP]M)", "%m/%d/%Y %I:%M %p"),
    # "2026-04-18 10:00:00"
    (r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", "%Y-%m-%d %H:%M:%S"),
]


def _parse_applescript_date(raw: str) -> float | None:
    """Parse AppleScript date string to Unix timestamp. Returns None on failure."""
    raw = raw.strip()
    for pattern, fmt in _DATE_PATTERNS:
        m = re.search(pattern, raw)
        if m:
            try:
                groups = m.groups()
                date_str = " ".join(groups)
                dt = datetime.strptime(date_str, fmt)
                # AppleScript returns local time — treat as local
                dt_local = dt.astimezone()
                return dt_local.timestamp()
            except ValueError:
                continue
    logger.debug("Could not parse calendar date: %r", raw)
    return None


def _parse_calendar_output(raw: str) -> list[dict]:
    """Parse pipe-separated AppleScript output into event dicts."""
    events = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 1)
        if len(parts) < 2:
            continue
        title = parts[0].strip()
        start_ts = _parse_applescript_date(parts[1])
        events.append({
            "title": title,
            "start_ts": start_ts,
            "start_raw": parts[1].strip(),
        })
    return events


MEETING_NOW_WINDOW = 5 * 60    # 5 min before → "meeting now"
MEETING_SOON_WINDOW = 30 * 60  # 30 min before → "meeting soon"


class CalendarIntegration(BaseIntegration):
    name = "calendar"

    def __init__(self) -> None:
        super().__init__()
        self._last_notified: set[str] = set()  # event keys already surfaced today

    async def poll(self) -> list[dict]:
        backend = AppleScriptBackend()
        action = Action(kind="calendar.list_today", params={})

        try:
            ok, raw_output, _ = await backend.execute(action)
        except Exception as e:
            self._fail(str(e))
            return []

        if not ok:
            self._fail(raw_output if isinstance(raw_output, str) else str(raw_output))
            return []

        events_raw = _parse_calendar_output(raw_output if raw_output else "")
        now = time.time()
        result: list[dict] = []

        for ev in events_raw:
            ts = ev.get("start_ts")
            title = ev.get("title", "")
            key = f"{title}_{ev.get('start_raw', '')}"

            if ts is None:
                continue

            time_until = ts - now
            event_type = None

            if -MEETING_NOW_WINDOW <= time_until <= MEETING_NOW_WINDOW:
                event_type = "meeting_now"
            elif MEETING_NOW_WINDOW < time_until <= MEETING_SOON_WINDOW:
                event_type = "meeting_soon"

            if event_type and key not in self._last_notified:
                result.append({
                    "type": "calendar_event",
                    "source": "calendar",
                    "event_type": event_type,
                    "title": title,
                    "start_ts": ts,
                    "minutes_until": int(time_until / 60),
                })
                self._last_notified.add(key)

        # Reset notified set daily
        if len(self._last_notified) > 200:
            self._last_notified.clear()

        self._ok({"events_today": len(events_raw), "signals_emitted": len(result)})
        return result

    def health_check(self) -> IntegrationHealth:
        return self._health
