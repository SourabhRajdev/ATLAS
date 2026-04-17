"""EMA-based signal feedback learning.

Each (source, kind) pair has a learned weight in [0,1] representing how often
the user actually wants to see suggestions from this signal. Feedback is a
boolean (accepted/dismissed); the weight updates by exponential moving average.
"""

from __future__ import annotations

import json
from pathlib import Path

STATE_FILE = Path("~/.atlas/signal_weights.json").expanduser()
ALPHA = 0.2          # EMA smoothing — higher = faster to forget
DEFAULT = 0.5        # neutral prior


class SignalLearner:
    def __init__(self) -> None:
        self._weights = self._load()

    def _load(self) -> dict[str, float]:
        if not STATE_FILE.exists():
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            return {}
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}

    def _save(self) -> None:
        STATE_FILE.write_text(json.dumps(self._weights))

    @staticmethod
    def _key(source: str, kind: str) -> str:
        return f"{source}:{kind}"

    def weight(self, source: str, kind: str) -> float:
        return self._weights.get(self._key(source, kind), DEFAULT)

    def feedback(self, source: str, kind: str, accepted: bool) -> float:
        key = self._key(source, kind)
        prev = self._weights.get(key, DEFAULT)
        target = 1.0 if accepted else 0.0
        new = (1 - ALPHA) * prev + ALPHA * target
        self._weights[key] = new
        self._save()
        return new
