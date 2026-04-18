"""InterruptBudget — token bucket anti-fatigue system.

Replenishes at 1 token/hour. Costs: critical=1, high=2, medium=4, low=8.
ALWAYS_INTERRUPT signals bypass the bucket entirely.
NEVER_INTERRUPT_WHEN states queue the signal for later.

This is the most important part of the proactive system.
Without it, every low-priority signal would interrupt the user constantly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from atlas.proactive.signals import ALWAYS_INTERRUPT, Priority, Signal

# Cost in tokens per interrupt priority level
_COST: dict[str, float] = {
    Priority.CRITICAL: 1.0,
    Priority.HIGH: 2.0,
    Priority.MEDIUM: 4.0,
    Priority.LOW: 8.0,
}

# State conditions under which no interrupts happen (except ALWAYS_INTERRUPT)
NEVER_INTERRUPT_WHEN = frozenset({
    "in_meeting",
    "presenting",
    "screen_sharing",
})


@dataclass
class InterruptBudget:
    tokens: float = 10.0
    max_tokens: float = 10.0
    replenish_rate: float = 1.0  # tokens per hour
    last_replenish: float = field(default_factory=time.time)

    def replenish(self) -> None:
        """Add tokens for time elapsed since last replenish."""
        now = time.time()
        elapsed_hours = (now - self.last_replenish) / 3600.0
        added = elapsed_hours * self.replenish_rate
        self.tokens = min(self.max_tokens, self.tokens + added)
        self.last_replenish = now

    def can_afford(self, signal: Signal) -> bool:
        """Return True if the bucket has enough tokens for this signal's priority."""
        self.replenish()
        cost = _COST.get(signal.priority_label(), 4.0)
        return self.tokens >= cost

    def deduct(self, signal: Signal) -> None:
        """Spend tokens for surfacing this signal."""
        cost = _COST.get(signal.priority_label(), 4.0)
        self.tokens = max(0.0, self.tokens - cost)

    def to_dict(self) -> dict:
        self.replenish()
        return {
            "tokens": round(self.tokens, 2),
            "max_tokens": self.max_tokens,
            "replenish_rate_per_hour": self.replenish_rate,
        }


class InterruptGate:
    """Single point that decides: interrupt now, queue, or drop."""

    def __init__(self, budget: InterruptBudget) -> None:
        self._budget = budget
        self._user_state: set[str] = set()  # active states from PerceptionDaemon

    def update_user_state(self, states: set[str]) -> None:
        self._user_state = states

    def evaluate(self, signal: Signal) -> str:
        """
        Returns one of:
          "interrupt" — surface to user now
          "queue"     — save for later
          "drop"      — signal expired or irrelevant, discard
        """
        if signal.is_expired():
            return "drop"

        # ALWAYS_INTERRUPT: bypass everything
        if signal.type in ALWAYS_INTERRUPT:
            self._budget.deduct(signal)
            return "interrupt"

        # NEVER_INTERRUPT_WHEN: queue (not drop — it matters)
        if self._user_state & NEVER_INTERRUPT_WHEN:
            return "queue"

        # Budget check
        if self._budget.can_afford(signal):
            self._budget.deduct(signal)
            return "interrupt"

        return "queue"
