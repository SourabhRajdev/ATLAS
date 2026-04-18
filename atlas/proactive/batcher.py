"""Signal batcher — groups queued signals into a single interrupt.

If 3+ low/medium signals have been queued for 15 minutes without surfacing,
batch them into one interrupt: "3 things need your attention: [list]"
This prevents the user from being interrupted 5 times for minor things.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from atlas.proactive.signals import Priority, Signal

BATCH_WINDOW_SECONDS = 15 * 60  # 15 minutes
MIN_BATCH_SIZE = 3


@dataclass
class SignalBatch:
    signals: list[Signal]
    created_at: float = field(default_factory=time.time)

    def summary(self) -> str:
        n = len(self.signals)
        items = []
        for sig in self.signals[:5]:
            action = sig.suggested_action or sig.type.replace("_", " ")
            items.append(f"• {action}")
        text = f"{n} thing{'s' if n > 1 else ''} need{'s' if n == 1 else ''} your attention:\n"
        text += "\n".join(items)
        if len(self.signals) > 5:
            text += f"\n…and {len(self.signals) - 5} more"
        return text

    def highest_priority(self) -> Signal:
        return max(self.signals, key=lambda s: s.effective_priority())


class SignalBatcher:
    def __init__(self) -> None:
        self._queue: list[Signal] = []
        self._last_batch_at: float = 0.0

    def enqueue(self, signal: Signal) -> None:
        """Add a signal to the batch queue."""
        if not signal.is_expired():
            self._queue.append(signal)

    def prune_expired(self) -> int:
        """Remove expired signals. Returns count removed."""
        before = len(self._queue)
        self._queue = [s for s in self._queue if not s.is_expired()]
        return before - len(self._queue)

    def should_batch_now(self) -> bool:
        """Return True if it's time to batch and surface queued signals."""
        self.prune_expired()
        if len(self._queue) < MIN_BATCH_SIZE:
            return False
        # Only batch if batch window has elapsed since last batch
        return (time.time() - self._last_batch_at) >= BATCH_WINDOW_SECONDS

    def pop_batch(self) -> SignalBatch | None:
        """Pop the current batch. Returns None if not enough signals."""
        self.prune_expired()
        if len(self._queue) < MIN_BATCH_SIZE:
            return None
        batch_signals = list(self._queue)
        self._queue = []
        self._last_batch_at = time.time()
        return SignalBatch(signals=batch_signals)

    def queue_size(self) -> int:
        return len(self._queue)

    def apply_decay(self, now: float | None = None) -> list[Signal]:
        """
        Apply signal decay rules:
        - LOW priority unacted > 2h → archive (remove from queue)
        - MEDIUM priority unacted > 4h → archive
        - HIGH priority unacted > 1h → escalate to priority boost +0.2
        - CRITICAL → never decayed here (handled by engine re-interrupt loop)

        Returns list of signals that were escalated.
        """
        now = now or time.time()
        to_remove: list[Signal] = []
        escalated: list[Signal] = []

        for sig in self._queue:
            age_s = now - sig.created_at
            prio = sig.priority_label()

            if prio == Priority.LOW and age_s > 2 * 3600:
                to_remove.append(sig)
            elif prio == Priority.MEDIUM and age_s > 4 * 3600:
                to_remove.append(sig)
            elif prio == Priority.HIGH and age_s > 1 * 3600:
                sig.priority_boost += 0.2  # escalate toward critical
                escalated.append(sig)

        for sig in to_remove:
            sig.outcome = "archived"
            self._queue.remove(sig)

        return escalated
