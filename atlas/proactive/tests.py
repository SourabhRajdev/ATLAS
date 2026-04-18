"""Proactive Intelligence tests.

Run: python3 -m atlas.proactive.tests
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

_PASS = 0
_FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f" | {detail}" if detail else ""))


async def run_tests() -> None:
    print("=" * 60)
    print("Proactive Intelligence Test Suite")
    print("=" * 60)

    from atlas.proactive.signals import Signal, SignalType, Priority, ALWAYS_INTERRUPT
    from atlas.proactive.budget import InterruptBudget, InterruptGate
    from atlas.proactive.batcher import SignalBatcher, MIN_BATCH_SIZE
    from atlas.proactive.learning import FeedbackLearner
    from atlas.proactive.engine import ProactiveEngine

    # ── Test 1: Signal creation ──────────────────────────────────────────
    print("\n[1] Signal Creation")

    sig = Signal.create(
        type=SignalType.EMAIL_URGENT,
        source="gmail",
        payload={"subject": "URGENT: Server down"},
        urgency=0.9,
    )
    check("signal has id", bool(sig.id))
    check("signal priority is float", isinstance(sig.priority, float))
    check("email_urgent priority >= 0.65", sig.priority >= 0.65, f"got {sig.priority}")
    check("signal not expired", not sig.is_expired())
    check("priority label is HIGH", sig.priority_label() == Priority.HIGH,
          f"got {sig.priority_label()}")

    # Test expiry
    expired_sig = Signal.create(
        type=SignalType.COMMIT_STALE,
        source="git",
        payload={},
        ttl_seconds=0.001,
    )
    await asyncio.sleep(0.01)
    check("expired signal detected", expired_sig.is_expired())

    # ── Test 2: ALWAYS_INTERRUPT bypasses budget ─────────────────────────
    print("\n[2] ALWAYS_INTERRUPT Bypass")

    budget = InterruptBudget(tokens=0.0)  # empty budget
    gate = InterruptGate(budget)

    battery_sig = Signal.create(
        type=SignalType.BATTERY_CRITICAL,
        source="system",
        payload={"level": 5},
    )
    check("BATTERY_CRITICAL is in ALWAYS_INTERRUPT", SignalType.BATTERY_CRITICAL in ALWAYS_INTERRUPT)
    verdict = gate.evaluate(battery_sig)
    check("BATTERY_CRITICAL interrupts despite empty budget", verdict == "interrupt",
          f"verdict={verdict}")

    meeting_sig = Signal.create(
        type=SignalType.MEETING_NOW,
        source="calendar",
        payload={"title": "Sprint planning"},
    )
    verdict2 = gate.evaluate(meeting_sig)
    check("MEETING_NOW interrupts despite empty budget", verdict2 == "interrupt",
          f"verdict={verdict2}")

    # ── Test 3: Budget gates low-priority signals ─────────────────────────
    print("\n[3] Budget Gates Low-Priority Signals")

    budget_low = InterruptBudget(tokens=2.0)  # only 2 tokens
    gate_low = InterruptGate(budget_low)

    low_sig = Signal.create(
        type=SignalType.BEHAVIOR_INSIGHT,
        source="improvement",
        payload={"insight": "You work faster in the morning"},
    )
    check("behavior_insight is LOW priority",
          low_sig.priority_label() == Priority.LOW, f"got {low_sig.priority_label()}")
    verdict3 = gate_low.evaluate(low_sig)
    check("LOW signal queued when budget insufficient (cost=8, tokens=2)",
          verdict3 == "queue", f"verdict={verdict3}")

    # Full budget → low signal should get through
    budget_full = InterruptBudget(tokens=10.0)
    gate_full = InterruptGate(budget_full)
    verdict4 = gate_full.evaluate(low_sig)
    check("LOW signal interrupts with full budget", verdict4 == "interrupt",
          f"verdict={verdict4}")

    # ── Test 4: NEVER_INTERRUPT_WHEN queues signals ──────────────────────
    print("\n[4] NEVER_INTERRUPT_WHEN State")

    budget_ok = InterruptBudget(tokens=10.0)
    gate_state = InterruptGate(budget_ok)
    gate_state.update_user_state({"in_meeting"})

    high_sig = Signal.create(
        type=SignalType.EMAIL_URGENT,
        source="gmail",
        payload={},
    )
    verdict5 = gate_state.evaluate(high_sig)
    check("HIGH signal queued when in_meeting", verdict5 == "queue", f"verdict={verdict5}")

    # Remove state → should interrupt now
    gate_state.update_user_state(set())
    verdict6 = gate_state.evaluate(high_sig)
    check("HIGH signal interrupts after leaving meeting", verdict6 == "interrupt",
          f"verdict={verdict6}")

    # ── Test 5: Batching ─────────────────────────────────────────────────
    print("\n[5] Signal Batching")

    batcher = SignalBatcher()
    batcher._last_batch_at = time.time() - (16 * 60)  # simulate 16 min ago

    for i in range(MIN_BATCH_SIZE + 1):
        s = Signal.create(
            type=SignalType.COMMIT_STALE,
            source="git",
            payload={"repo": f"repo_{i}"},
        )
        batcher.enqueue(s)

    check("queue has signals", batcher.queue_size() >= MIN_BATCH_SIZE,
          f"got {batcher.queue_size()}")
    check("should_batch_now() True after 16 min", batcher.should_batch_now())

    batch = batcher.pop_batch()
    check("batch created", batch is not None)
    check("batch has signals", len(batch.signals) >= MIN_BATCH_SIZE if batch else False)
    summary = batch.summary() if batch else ""
    check("batch summary mentions count", any(str(i) in summary for i in range(1, 10)),
          f"summary={summary[:60]}")
    check("queue cleared after pop", batcher.queue_size() == 0)

    # ── Test 6: Signal decay ─────────────────────────────────────────────
    print("\n[6] Signal Decay")

    batcher2 = SignalBatcher()

    # Add LOW signal created 3 hours ago (should be archived after 2h)
    old_low = Signal.create(type=SignalType.COMMIT_STALE, source="git", payload={})
    old_low.created_at = time.time() - (3 * 3600)
    batcher2.enqueue(old_low)

    # Add HIGH signal created 2 hours ago (should escalate after 1h)
    old_high = Signal.create(type=SignalType.EMAIL_URGENT, source="gmail", payload={})
    old_high.created_at = time.time() - (2 * 3600)
    batcher2.enqueue(old_high)

    # Add recent signal (should not be affected)
    recent = Signal.create(type=SignalType.COMMIT_STALE, source="git", payload={})
    batcher2.enqueue(recent)

    escalated = batcher2.apply_decay()
    check("old LOW signal archived", batcher2.queue_size() < 3,
          f"queue={batcher2.queue_size()}")
    check("HIGH signal escalated", len(escalated) >= 1 or old_high.priority_boost > 0,
          f"escalated={len(escalated)}, boost={old_high.priority_boost}")

    # ── Test 7: Learning feedback (20 sample threshold) ──────────────────
    print("\n[7] Learning Feedback (20-sample threshold)")

    with tempfile.TemporaryDirectory() as tmpdir:
        learner = FeedbackLearner(Path(tmpdir) / "proactive.db")

        test_sig = Signal.create(
            type=SignalType.BEHAVIOR_INSIGHT,
            source="improvement",
            payload={},
        )

        # Record 20 "dismissed" outcomes
        for i in range(20):
            learner.record_outcome(test_sig, "dismissed")

        delta = learner.get_weight_delta(SignalType.BEHAVIOR_INSIGHT)
        check("weight delta updated after 20 samples", delta != 0.0, f"delta={delta}")
        check("dismissed outcomes produce negative delta", delta < 0.0, f"delta={delta}")

        # Record 20 "acted" outcomes for a different type
        acted_sig = Signal.create(
            type=SignalType.EMAIL_URGENT,
            source="gmail",
            payload={},
        )
        for i in range(20):
            learner.record_outcome(acted_sig, "acted")

        acted_delta = learner.get_weight_delta(SignalType.EMAIL_URGENT)
        check("acted outcomes produce positive delta", acted_delta > 0.0, f"delta={acted_delta}")

        learner.close()

    # ── Test 8: Engine lifecycle ─────────────────────────────────────────
    print("\n[8] ProactiveEngine Lifecycle")

    with tempfile.TemporaryDirectory() as tmpdir:
        interrupted: list[str] = []

        def capture_interrupt(text: str) -> None:
            interrupted.append(text)

        engine = ProactiveEngine(
            data_dir=Path(tmpdir),
            inject_callback=capture_interrupt,
        )
        health_before = engine.health_check()
        check("engine unhealthy before start", health_before["status"] == "down")

        await engine.start()
        health_after = engine.health_check()
        check("engine healthy after start", health_after["status"] == "healthy")

        # Register a signal source that emits a critical signal
        def critical_source() -> list[Signal]:
            return [Signal.create(
                type=SignalType.BATTERY_CRITICAL,
                source="system",
                payload={"level": 3},
            )]

        engine.register_source(critical_source)

        # Manually trigger one cycle
        await engine._cycle()
        check("critical signal surfaced", len(interrupted) > 0,
              f"interrupted={interrupted}")
        check("surfaced text mentions battery", any("battery" in t.lower() or "critical" in t.lower()
              for t in interrupted))

        await engine.stop()
        check("engine stopped cleanly", not engine._running)

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
