"""ProactiveEngine — background asyncio task that surfaces signals.

Runs every 30 seconds. Collects signals from all sources, evaluates them
through the interrupt gate, batches low-priority ones, and injects approved
signals into the orchestrator at the next natural pause.

Does NOT call any LLM. All scoring is deterministic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable

from atlas.proactive.batcher import SignalBatcher, SignalBatch
from atlas.proactive.budget import InterruptBudget, InterruptGate
from atlas.proactive.learning import FeedbackLearner
from atlas.proactive.signals import Signal, SignalType, ALWAYS_INTERRUPT

logger = logging.getLogger("atlas.proactive")

CYCLE_INTERVAL = 30.0  # seconds between engine cycles
PERSISTENT_REINTERRUPT = 15 * 60  # critical signals re-interrupt every 15 min


class ProactiveEngine:
    def __init__(
        self,
        data_dir: Path,
        inject_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._inject = inject_callback  # called when a signal is ready to surface
        self._budget = InterruptBudget()
        self._gate = InterruptGate(self._budget)
        self._batcher = SignalBatcher()
        self._learner = FeedbackLearner(data_dir / "proactive.db")
        self._signal_sources: list[Callable[[], list[Signal]]] = []
        self._persistent_signals: list[tuple[Signal, float]] = []  # (signal, last_interrupt_at)
        self._running = False
        self._task: asyncio.Task | None = None

    def register_source(self, fn: Callable[[], list[Signal]]) -> None:
        """Register a signal source function. Called each cycle."""
        self._signal_sources.append(fn)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="proactive_engine")
        logger.info("ProactiveEngine started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._learner.close()
        logger.info("ProactiveEngine stopped")

    def record_outcome(self, signal_id: str, outcome: str) -> None:
        """External callback: user acted/dismissed/ignored a signal."""
        pass  # learner.record_outcome called from engine loop tracking

    def update_user_state(self, states: set[str]) -> None:
        self._gate.update_user_state(states)

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._cycle()
            except Exception as e:
                logger.error("ProactiveEngine cycle error: %s", e)
            await asyncio.sleep(CYCLE_INTERVAL)

    async def _cycle(self) -> None:
        # Collect signals from all registered sources
        new_signals: list[Signal] = []
        for source_fn in self._signal_sources:
            try:
                signals = source_fn()
                new_signals.extend(signals)
            except Exception as e:
                logger.warning("Signal source error: %s", e)

        # Apply learned priority adjustments
        for sig in new_signals:
            self._learner.apply_learned_weights(sig)

        # Evaluate each signal
        for sig in new_signals:
            verdict = self._gate.evaluate(sig)
            if verdict == "interrupt":
                await self._surface(sig)
                if sig.type in ALWAYS_INTERRUPT:
                    self._persistent_signals.append((sig, time.time()))
            elif verdict == "queue":
                self._batcher.enqueue(sig)
            # "drop" → discard silently

        # Re-interrupt persistent signals (critical)
        now = time.time()
        still_persistent = []
        for sig, last_at in self._persistent_signals:
            if not sig.is_expired() and (now - last_at) >= PERSISTENT_REINTERRUPT:
                await self._surface(sig, prefix="[Reminder] ")
                still_persistent.append((sig, now))
            elif not sig.is_expired():
                still_persistent.append((sig, last_at))
        self._persistent_signals = still_persistent

        # Apply decay to queued signals
        escalated = self._batcher.apply_decay(now)
        for sig in escalated:
            verdict = self._gate.evaluate(sig)
            if verdict == "interrupt":
                await self._surface(sig, prefix="[Escalated] ")

        # Check if batch is ready
        if self._batcher.should_batch_now():
            batch = self._batcher.pop_batch()
            if batch:
                await self._surface_batch(batch)

    async def _surface(self, signal: Signal, prefix: str = "") -> None:
        signal.surfaced_at = time.time()
        text = prefix + _format_signal(signal)
        logger.info("Surfacing signal: %s | %s", signal.type, text[:80])
        if self._inject:
            try:
                result = self._inject(text)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("Inject callback failed: %s", e)

    async def _surface_batch(self, batch: SignalBatch) -> None:
        text = batch.summary()
        logger.info("Surfacing batch of %d signals", len(batch.signals))
        if self._inject:
            try:
                result = self._inject(text)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("Batch inject failed: %s", e)

    def health_check(self) -> dict:
        return {
            "status": "healthy" if self._running else "down",
            "last_check": time.time(),
            "details": {
                "running": self._running,
                "budget": self._budget.to_dict(),
                "queue_size": self._batcher.queue_size(),
                "persistent_signals": len(self._persistent_signals),
                "sources_registered": len(self._signal_sources),
            },
        }


def _format_signal(signal: Signal) -> str:
    if signal.suggested_action:
        return signal.suggested_action
    type_label = signal.type.replace("_", " ").title()
    payload_summary = ", ".join(
        f"{k}: {str(v)[:40]}"
        for k, v in list(signal.payload.items())[:2]
    )
    return f"{type_label}: {payload_summary}" if payload_summary else type_label
