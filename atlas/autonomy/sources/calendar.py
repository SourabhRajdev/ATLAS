"""Calendar source — upcoming events via EventKit (or osascript fallback).

Emits:
  - meeting_soon (T-15, T-5, T-1 minutes)
  - meeting_now
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime

from atlas.autonomy.models import Signal

logger = logging.getLogger("atlas.autonomy.calendar")

_SCRIPT = '''
tell application "Calendar"
    set today to current date
    set hours of today to 0
    set minutes of today to 0
    set seconds of today to 0
    set horizon to today + (1 * days)
    set out to ""
    repeat with c in calendars
        repeat with e in (every event of c whose start date ≥ (current date) and start date < horizon)
            set out to out & (summary of e) & "§" & ((start date of e) as string) & linefeed
        end repeat
    end repeat
    return out
end tell
'''


class CalendarSource:
    source = "calendar"

    def __init__(self) -> None:
        self._last_fired: dict[str, float] = {}

    async def poll(self) -> list[Signal]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", _SCRIPT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return []
        except Exception as e:
            logger.debug("calendar poll failed: %s", e)
            return []

        signals: list[Signal] = []
        now = time.time()
        for line in stdout.decode("utf-8", "replace").splitlines():
            if "§" not in line:
                continue
            title, date_str = line.split("§", 1)
            start_ts = _parse_applescript_date(date_str.strip())
            if start_ts is None:
                continue
            delta = start_ts - now
            kind = None
            if -60 < delta <= 60:
                kind = "meeting_now"
            elif 60 < delta <= 90:
                kind = "meeting_t1"
            elif 240 < delta <= 330:
                kind = "meeting_t5"
            elif 840 < delta <= 930:
                kind = "meeting_t15"
            if kind is None:
                continue
            key = f"{title}:{kind}"
            if now - self._last_fired.get(key, 0) < 300:
                continue
            self._last_fired[key] = now
            signals.append(Signal(
                source=self.source,
                kind=kind,
                payload={"title": title.strip(), "start_ts": start_ts, "in_seconds": int(delta)},
            ))
        return signals


_DATE_RX = re.compile(r"(\w+), (\w+) (\d+), (\d+) at (\d+):(\d+):(\d+) (AM|PM)")


def _parse_applescript_date(s: str) -> float | None:
    m = _DATE_RX.search(s)
    if not m:
        return None
    _, mon, day, year, hour, minute, sec, ampm = m.groups()
    try:
        dt = datetime.strptime(
            f"{mon} {day} {year} {hour}:{minute}:{sec} {ampm}",
            "%B %d %Y %I:%M:%S %p",
        )
        return dt.timestamp()
    except ValueError:
        return None
