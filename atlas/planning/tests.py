"""Long-Horizon Planning tests.

Run: python3 -m atlas.planning.tests
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
    print("Long-Horizon Planning Test Suite")
    print("=" * 60)

    from atlas.planning.models import Goal, GoalStatus, Priority, Task, TaskStatus
    from atlas.planning.store import PlanningStore
    from atlas.planning.inference import InferenceEngine, detect_goal_type
    from atlas.planning.replanner import WeeklyReplanner
    from atlas.planning.engine import PlanningEngine

    # ── Test 1: Goal model ────────────────────────────────────────────────
    print("\n[1] Goal Model")

    goal = Goal.create(
        title="Launch MVP of Atlas",
        description="Build and ship the Atlas assistant",
        priority=Priority.HIGH,
        due_date=time.time() + 30 * 86400,
        success_criteria="All 8 systems passing tests",
    )
    check("goal has id", bool(goal.id))
    check("goal status is active", goal.status == GoalStatus.ACTIVE)
    check("goal not overdue", not goal.is_overdue())
    check("days_until_due is ~30", abs(goal.days_until_due() - 30) < 1,
          f"got {goal.days_until_due():.1f}")

    overdue_goal = Goal.create(title="Old goal", due_date=time.time() - 86400)
    check("past due_date → is_overdue()", overdue_goal.is_overdue())
    check("no deadline → days_until_due is None",
          Goal.create(title="Open-ended").days_until_due() is None)

    # ── Test 2: Task model ────────────────────────────────────────────────
    print("\n[2] Task Model")

    t1 = Task.create(goal_id=goal.id, title="Write spec", estimated_minutes=60)
    t2 = Task.create(goal_id=goal.id, title="Build MVP", depends_on=[t1.id], estimated_minutes=240)

    check("task has id", bool(t1.id))
    check("t1 not blocked (no deps)", not t1.is_blocked(set()))
    check("t2 blocked by t1", t2.is_blocked(set()))
    check("t2 unblocked after t1 complete", not t2.is_blocked({t1.id}))

    # ── Test 3: PlanningStore CRUD ────────────────────────────────────────
    print("\n[3] PlanningStore CRUD")

    with tempfile.TemporaryDirectory() as tmpdir:
        store = PlanningStore(Path(tmpdir) / "planning.db")

        # Goals
        store.save_goal(goal)
        fetched = store.get_goal(goal.id)
        check("goal round-trips", fetched is not None and fetched.id == goal.id)
        check("goal title preserved", fetched.title == goal.title)
        check("goal priority preserved", fetched.priority == Priority.HIGH)
        check("goal tags preserved", fetched.tags == [])

        active = store.get_active_goals()
        check("active goals returns saved goal", any(g.id == goal.id for g in active))

        # Tasks
        store.save_task(t1)
        store.save_task(t2)
        tasks = store.get_tasks_for_goal(goal.id)
        check("tasks for goal returns both", len(tasks) == 2, f"got {len(tasks)}")

        # Complete task → progress updates
        result = store.complete_task(t1.id, actual_minutes=45)
        check("complete_task returns True", result)
        check("second complete is no-op", not store.complete_task(t1.id))

        updated = store.get_task(t1.id)
        check("completed task has status=completed", updated.status == TaskStatus.COMPLETED)
        check("actual_minutes stored", updated.actual_minutes == 45)

        progress = store.update_goal_progress(goal.id)
        check("goal progress = 0.5 after 1/2 tasks done", abs(progress - 0.5) < 0.01,
              f"got {progress:.2f}")

        # Pending tasks
        pending = store.get_pending_tasks()
        check("pending_tasks excludes completed", all(
            t.status != TaskStatus.COMPLETED for t in pending
        ))

        store.close()

    # ── Test 4: InferenceEngine decomposition ─────────────────────────────
    print("\n[4] InferenceEngine Decomposition")

    engine = InferenceEngine()

    proj_goal = Goal.create(title="Build a web app", description="Create a full-stack app")
    proj_tasks = engine.decompose(proj_goal)
    check("project goal → tasks generated", len(proj_tasks) >= 3, f"got {len(proj_tasks)}")
    check("tasks have goal_id set", all(t.goal_id == proj_goal.id for t in proj_tasks))
    check("first task unblocked", not proj_tasks[0].is_blocked(set()))

    learn_goal = Goal.create(title="Learn Rust programming", description="Study Rust")
    check("learning keyword detected", detect_goal_type(learn_goal) == "learning",
          detect_goal_type(learn_goal))

    write_goal = Goal.create(title="Write a technical blog post")
    check("writing keyword detected", detect_goal_type(write_goal) == "writing",
          detect_goal_type(write_goal))

    fitness_goal = Goal.create(title="Train for marathon")
    check("fitness keyword detected", detect_goal_type(fitness_goal) == "fitness",
          detect_goal_type(fitness_goal))

    habit_goal = Goal.create(title="Build a daily meditation habit")
    check("habit keyword detected", detect_goal_type(habit_goal) == "habit",
          detect_goal_type(habit_goal))

    # Suggest next actions respects dependencies
    with tempfile.TemporaryDirectory() as tmpdir:
        store2 = PlanningStore(Path(tmpdir) / "p.db")
        store2.save_goal(proj_goal)
        for t in proj_tasks:
            store2.save_task(t)

        next_actions = engine.suggest_next(proj_goal, proj_tasks)
        check("suggest_next returns unblocked tasks", len(next_actions) >= 1)
        for t in next_actions:
            completed = {pt.id for pt in proj_tasks if pt.status == TaskStatus.COMPLETED}
            check(f"suggested task '{t.title}' is unblocked", not t.is_blocked(completed))

        store2.close()

    # Estimate completion
    est = engine.estimate_completion(proj_goal, proj_tasks)
    check("estimate_completion returns future timestamp", est is None or est > time.time())

    # ── Test 5: WeeklyReplanner ───────────────────────────────────────────
    print("\n[5] WeeklyReplanner")

    with tempfile.TemporaryDirectory() as tmpdir:
        store3 = PlanningStore(Path(tmpdir) / "p.db")
        replanner = WeeklyReplanner(store3)

        # No goals → graceful message
        briefing = replanner.run()
        check("empty goals → helpful message", "No active goals" in briefing, briefing[:60])

        # Add goals + tasks
        g = Goal.create(title="Ship feature X", priority=Priority.HIGH,
                        due_date=time.time() + 14 * 86400)
        store3.save_goal(g)

        task_a = Task.create(g.id, "Write tests", estimated_minutes=60)
        task_b = Task.create(g.id, "Implement feature", estimated_minutes=180)
        task_c = Task.create(g.id, "Deploy", estimated_minutes=30, depends_on=[task_b.id])
        for t in [task_a, task_b, task_c]:
            store3.save_task(t)

        briefing2 = replanner.run()
        check("briefing contains goal tasks", "Write tests" in briefing2 or "Implement" in briefing2,
              briefing2[:100])
        check("briefing shows capacity info", "h /" in briefing2 or "Estimated" in briefing2,
              briefing2[:100])

        # At-risk goal detection
        stale_goal = Goal.create(title="Forgotten project", priority=Priority.MEDIUM)
        stale_goal.created_at = time.time() - 20 * 86400  # created 20 days ago
        store3.save_goal(stale_goal)
        stale_task = Task.create(stale_goal.id, "Do the thing")
        store3.save_task(stale_task)

        at_risk = replanner._detect_at_risk([stale_goal])
        check("stale goal detected as at-risk", len(at_risk) >= 1,
              f"got {len(at_risk)}")

        store3.close()

    # ── Test 6: PlanningEngine end-to-end ────────────────────────────────
    print("\n[6] PlanningEngine End-to-End")

    with tempfile.TemporaryDirectory() as tmpdir:
        pe = PlanningEngine(Path(tmpdir))

        health = pe.health_check()
        check("engine healthy", health["status"] == "healthy", str(health))

        # Create goal with auto-decompose
        goal2, tasks2 = pe.create_goal(
            title="Learn machine learning",
            description="Study ML algorithms and build models",
            priority=Priority.HIGH,
        )
        check("goal created", goal2.id is not None)
        check("tasks auto-generated", len(tasks2) >= 3, f"got {len(tasks2)}")

        # Get next actions
        next_up = pe.get_next_actions(goal2.id)
        check("next actions returned", len(next_up) >= 1)
        check("next action belongs to goal", all(t.goal_id == goal2.id for t in next_up))

        # Complete first task
        first = next_up[0]
        ok = pe.complete_task(first.id, actual_minutes=55)
        check("task completed successfully", ok)

        # Cross-goal next actions
        pe.create_goal("Build fitness routine", priority=Priority.MEDIUM)
        cross_goal_next = pe.get_next_actions()
        check("cross-goal next actions", len(cross_goal_next) >= 1)

        # Weekly plan
        briefing3 = pe.run_weekly_plan()
        check("weekly plan generated", bool(briefing3))
        check("weekly plan has content", len(briefing3) > 20)

        # Current week status
        status = pe.current_week_status()
        check("current week status returned", bool(status))

        # Abandon goal
        ok2 = pe.abandon_goal(goal2.id)
        check("goal abandoned", ok2)
        g_check = pe.get_goal(goal2.id)
        check("goal status is abandoned", g_check.status == GoalStatus.ABANDONED)

        # health_check shows counts
        h2 = pe.health_check()
        check("health shows active_goals", "active_goals" in h2)

        pe.close()

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    run_tests()
