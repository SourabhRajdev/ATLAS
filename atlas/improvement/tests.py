"""Self-Improvement Loop tests.

Run: python3 -m atlas.improvement.tests
"""

from __future__ import annotations

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


def run_tests() -> None:
    print("=" * 60)
    print("Self-Improvement Loop Test Suite")
    print("=" * 60)

    from atlas.improvement.models import (
        QualitySignal, SignalKind, ImpactLevel, BehaviorPattern
    )
    from atlas.improvement.monitor import BehaviorMonitor, classify_user_message
    from atlas.improvement.analyzer import BehaviorAnalyzer
    from atlas.improvement.engine import SelfImprovementEngine

    # ── Test 1: QualitySignal model ───────────────────────────────────────
    print("\n[1] QualitySignal Model")

    sig = QualitySignal.create(
        kind=SignalKind.TOOL_ERROR_SPIKE,
        impact=ImpactLevel.NEGATIVE,
        context="web_search",
        detail="rate limit exceeded",
        value=0.25,
    )
    check("signal has id", bool(sig.id))
    check("kind is correct", sig.kind == SignalKind.TOOL_ERROR_SPIKE)
    check("impact is negative", sig.impact == ImpactLevel.NEGATIVE)
    check("recorded_at is set", sig.recorded_at > 0)

    # BehaviorPattern
    pat = BehaviorPattern(
        pattern_type="tool_error_spike",
        frequency=5,
        impact=ImpactLevel.NEGATIVE,
        contexts=["web_search", "gmail"],
        first_seen=time.time() - 3600,
        last_seen=time.time(),
    )
    check("pattern is_concerning (negative + freq>=3)", pat.is_concerning)

    pos_pat = BehaviorPattern(
        pattern_type="positive_feedback",
        frequency=5,
        impact=ImpactLevel.POSITIVE,
        contexts=["chat"],
        first_seen=time.time() - 3600,
        last_seen=time.time(),
    )
    check("positive pattern is NOT concerning", not pos_pat.is_concerning)

    # ── Test 2: classify_user_message ────────────────────────────────────
    print("\n[2] Message Classification")

    check("'good job' → positive", classify_user_message("good job") == ImpactLevel.POSITIVE)
    check("'perfect!' → positive", classify_user_message("perfect!") == ImpactLevel.POSITIVE)
    check("'that's wrong' → negative", classify_user_message("that's wrong") == ImpactLevel.NEGATIVE)
    check("'no that's not right' → negative",
          classify_user_message("no that's not right") == ImpactLevel.NEGATIVE)
    check("'what time is it' → None", classify_user_message("what time is it") is None)
    check("'yes exactly!' → positive", classify_user_message("yes exactly!") == ImpactLevel.POSITIVE)

    # ── Test 3: BehaviorMonitor ───────────────────────────────────────────
    print("\n[3] BehaviorMonitor")

    with tempfile.TemporaryDirectory() as tmpdir:
        monitor = BehaviorMonitor(Path(tmpdir) / "imp.db")

        # Record signals
        monitor.record(sig)
        monitor.record_task_timing("Build feature", estimated_minutes=30, actual_minutes=90)
        monitor.record_task_timing("Quick fix", estimated_minutes=10, actual_minutes=12)  # no overrun
        monitor.record_tool_error("web_search", "timeout")
        monitor.record_user_feedback("that's wrong again", context="code_gen")
        monitor.record_user_feedback("great work!", context="planning")
        monitor.record_user_feedback("what's next?")  # neutral → not recorded
        monitor.record_goal_event("Ship MVP", completed=True)
        monitor.record_goal_event("Learn piano", completed=False)

        # Retrieve signals
        all_sigs = monitor.get_recent_signals(days=1)
        check("signals stored and retrieved", len(all_sigs) >= 5, f"got {len(all_sigs)}")

        # Kind filter
        task_sigs = monitor.get_recent_signals(days=1, kind=SignalKind.TASK_DURATION_OVERRUN)
        check("task overrun signal recorded", len(task_sigs) >= 1, f"got {len(task_sigs)}")
        check("quick fix did NOT create overrun signal",
              all(s.value >= 2.0 for s in task_sigs), f"values={[s.value for s in task_sigs]}")

        # Feedback signals
        neg_sigs = monitor.get_recent_signals(days=1, kind=SignalKind.NEGATIVE_FEEDBACK)
        pos_sigs = monitor.get_recent_signals(days=1, kind=SignalKind.POSITIVE_FEEDBACK)
        check("negative feedback recorded", len(neg_sigs) >= 1)
        check("positive feedback recorded", len(pos_sigs) >= 1)

        # Goal events
        goal_done = monitor.get_recent_signals(days=1, kind=SignalKind.GOAL_COMPLETED)
        goal_aban = monitor.get_recent_signals(days=1, kind=SignalKind.GOAL_ABANDONED)
        check("goal completed recorded", len(goal_done) == 1)
        check("goal abandoned recorded", len(goal_aban) == 1)

        # Counts
        counts = monitor.get_signal_counts(days=1)
        check("signal counts dict returned", isinstance(counts, dict))
        check("total count matches", sum(counts.values()) >= 5,
              f"total={sum(counts.values())}")

        # Duplicate signal id → ignored
        monitor.record(sig)  # same id again
        all_sigs2 = monitor.get_recent_signals(days=1)
        dup_check = sum(1 for s in all_sigs2 if s.id == sig.id)
        check("duplicate signal id ignored", dup_check == 1, f"got {dup_check} copies")

        monitor.close()

    # ── Test 4: BehaviorAnalyzer pattern detection ────────────────────────
    print("\n[4] BehaviorAnalyzer Pattern Detection")

    with tempfile.TemporaryDirectory() as tmpdir:
        monitor2 = BehaviorMonitor(Path(tmpdir) / "imp.db")
        analyzer = BehaviorAnalyzer(monitor2)

        # Plant 5 tool errors (>= 3 threshold)
        for i in range(5):
            monitor2.record(QualitySignal.create(
                kind=SignalKind.TOOL_ERROR_SPIKE,
                impact=ImpactLevel.NEGATIVE,
                context=f"web_search",
                detail=f"error #{i}",
            ))

        # Plant 4 user corrections
        for i in range(4):
            monitor2.record(QualitySignal.create(
                kind=SignalKind.USER_CORRECTION,
                impact=ImpactLevel.NEGATIVE,
                context="code_generation",
            ))

        # Plant 2 positive signals (below threshold)
        for i in range(2):
            monitor2.record(QualitySignal.create(
                kind=SignalKind.POSITIVE_FEEDBACK,
                impact=ImpactLevel.POSITIVE,
                context="planning",
            ))

        signals = monitor2.get_recent_signals(days=1)
        patterns = analyzer.identify_patterns(signals)

        check("patterns detected", len(patterns) >= 2, f"got {len(patterns)}")
        pattern_types = {p.pattern_type for p in patterns}
        check("tool_error_spike pattern found",
              SignalKind.TOOL_ERROR_SPIKE.value in pattern_types, f"got {pattern_types}")
        check("user_correction pattern found",
              SignalKind.USER_CORRECTION.value in pattern_types, f"got {pattern_types}")
        check("positive_feedback below threshold (2 < 3) not in patterns",
              SignalKind.POSITIVE_FEEDBACK.value not in pattern_types)

        # Negative patterns sort before positive
        neg_patterns = [p for p in patterns if p.impact == ImpactLevel.NEGATIVE]
        check("negative patterns sorted first", patterns[0].impact == ImpactLevel.NEGATIVE
              if neg_patterns else True)

        # Patterns with recommendations
        for p in patterns:
            if p.is_concerning:
                check(f"{p.pattern_type} has recommendation", bool(p.recommendation),
                      f"recommendation='{p.recommendation}'")

        monitor2.close()

    # ── Test 5: WeeklyReport generation ──────────────────────────────────
    print("\n[5] WeeklyReport Generation")

    with tempfile.TemporaryDirectory() as tmpdir:
        monitor3 = BehaviorMonitor(Path(tmpdir) / "imp.db")
        analyzer3 = BehaviorAnalyzer(monitor3)

        # No signals
        report_empty = analyzer3.generate_weekly_report()
        check("empty report generated", report_empty is not None)
        check("empty report health_score is 1.0", report_empty.health_score == 1.0)
        check("empty report has recommendation", len(report_empty.recommendations) >= 1)

        # Add mixed signals
        for _ in range(6):
            monitor3.record(QualitySignal.create(
                SignalKind.POSITIVE_FEEDBACK, ImpactLevel.POSITIVE, "chat"))
        for _ in range(4):
            monitor3.record(QualitySignal.create(
                SignalKind.USER_CORRECTION, ImpactLevel.NEGATIVE, "code"))
        for _ in range(3):
            monitor3.record(QualitySignal.create(
                SignalKind.TOOL_ERROR_SPIKE, ImpactLevel.NEGATIVE, "web_search"))

        report = analyzer3.generate_weekly_report()
        check("report has signals", report.total_signals >= 10)
        check("positive count correct", report.positive_count == 6, f"got {report.positive_count}")
        check("negative count correct", report.negative_count >= 4, f"got {report.negative_count}")
        check("health_score between 0 and 1",
              0.0 <= report.health_score <= 1.0, f"got {report.health_score:.2f}")
        check("summary is non-empty", len(report.summary) > 20)
        check("has recommendations", len(report.recommendations) >= 1)

        # Report was persisted
        saved = monitor3.get_report(report.week_key)
        check("report persisted to DB", saved is not None)

        monitor3.close()

    # ── Test 6: SelfImprovementEngine end-to-end ─────────────────────────
    print("\n[6] SelfImprovementEngine End-to-End")

    with tempfile.TemporaryDirectory() as tmpdir:
        eng = SelfImprovementEngine(Path(tmpdir))

        health = eng.health_check()
        check("engine healthy", health["status"] == "healthy", str(health))
        check("health has signals_last_7d", "signals_last_7d" in health)

        # Ingest via high-level methods
        eng.on_task_timing("Refactor auth", estimated_min=30, actual_min=90)
        eng.on_tool_error("github_api", "403 forbidden")
        eng.on_user_message("great, exactly what I wanted!", context="planning")
        eng.on_user_message("no that's completely wrong", context="code")
        eng.on_user_correction("response format", "too verbose")
        eng.on_goal_event("Launch feature", completed=True)

        sigs = eng.get_recent_signals(days=1)
        check("signals ingested via engine", len(sigs) >= 5, f"got {len(sigs)}")

        counts = eng.get_signal_counts(days=1)
        check("signal counts returned", sum(counts.values()) >= 5)

        report = eng.generate_report()
        check("report generated", report is not None)
        check("report week_key set", bool(report.week_key))
        check("report has signals", report.total_signals >= 5)

        eng.close()

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    run_tests()
