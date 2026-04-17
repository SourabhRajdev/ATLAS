"""Notification budget — ATLAS must be silent unless it actually matters.

Caps: 1 per 2 hours rolling, 6 per day. Quiet hours configurable. The
confidence score must exceed a threshold to spend budget.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

STATE_FILE = Path("~/.atlas/budget.json").expanduser()

MIN_INTERVAL_S = 2 * 3600      # 2 hours
DAILY_CAP = 6
MIN_CONFIDENCE = 0.55
QUIET_HOURS = (22, 7)          # 10pm - 7am local


class NotificationBudget:
    def __init__(self) -> None:
        self._state = self._load()

    def _load(self) -> dict:
        if not STATE_FILE.exists():
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            return {"history": []}
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {"history": []}

    def _save(self) -> None:
        STATE_FILE.write_text(json.dumps(self._state))

    def _prune(self) -> None:
        cutoff = time.time() - 24 * 3600
        self._state["history"] = [t for t in self._state["history"] if t > cutoff]

    def can_notify(self, confidence: float, now: float | None = None) -> tuple[bool, str]:
        now = now or time.time()
        if confidence < MIN_CONFIDENCE:
            return False, f"confidence {confidence:.2f} < {MIN_CONFIDENCE}"
        hour = datetime.fromtimestamp(now).hour
        start, end = QUIET_HOURS
        in_quiet = hour >= start or hour < end
        if in_quiet:
            return False, f"quiet hours ({hour}:00)"
        self._prune()
        hist = self._state["history"]
        if hist and now - hist[-1] < MIN_INTERVAL_S:
            return False, f"rate limit ({int((now - hist[-1])/60)}m since last)"
        if len(hist) >= DAILY_CAP:
            return False, f"daily cap ({DAILY_CAP}) reached"
        return True, ""

    def record(self, now: float | None = None) -> None:
        self._state["history"].append(now or time.time())
        self._save()
