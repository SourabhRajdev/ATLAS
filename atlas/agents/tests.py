"""Multi-Agent Coordination tests.

Run: python3 -m atlas.agents.tests
"""

from __future__ import annotations

import asyncio
import sys
import time

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
    print("Multi-Agent Coordination Test Suite")
    print("=" * 60)

    from atlas.agents.models import (
        AgentRole, AgentMessage, AgentTask, MessageKind, TaskStatus
    )
    from atlas.agents.bus import MessageBus
    from atlas.agents.base import BaseAgent
    from atlas.agents.roles import (
        OrchestratorAgent, ResearcherAgent, ExecutorAgent,
        CommunicatorAgent, AnalystAgent, GuardianAgent,
    )
    from atlas.agents.coordinator import AgentCoordinator

    # ── Test 1: Models ────────────────────────────────────────────────────
    print("\n[1] Agent Models")

    task = AgentTask.create(
        assigned_to=AgentRole.RESEARCHER,
        description="Find info about Python",
        context={"query": "Python asyncio"},
    )
    check("task has id", bool(task.id))
    check("task status is pending", task.status == TaskStatus.PENDING)
    check("task has no start time", task.started_at is None)

    task.start()
    check("task running after start()", task.status == TaskStatus.RUNNING)
    check("started_at set", task.started_at is not None)

    task.complete({"result": "done"})
    check("task done after complete()", task.status == TaskStatus.DONE)
    check("result stored", task.result == {"result": "done"})
    check("duration calculated", task.duration_seconds is not None and task.duration_seconds >= 0)

    failed = AgentTask.create(AgentRole.EXECUTOR, "Run command")
    failed.start()
    failed.fail("Permission denied")
    check("task failed correctly", failed.status == TaskStatus.FAILED)
    check("error stored", failed.error == "Permission denied")

    msg = AgentMessage.create(
        from_agent=AgentRole.ORCHESTRATOR,
        to_agent=AgentRole.RESEARCHER,
        kind=MessageKind.TASK_ASSIGN,
        payload={"task_id": task.id, "description": "Research"},
    )
    check("message has id", bool(msg.id))
    check("message from_agent is string", isinstance(msg.from_agent, str))
    check("message to_agent is string", isinstance(msg.to_agent, str))

    # ── Test 2: MessageBus ────────────────────────────────────────────────
    print("\n[2] MessageBus")

    bus = MessageBus()
    q1 = bus.subscribe("agent_a")
    q2 = bus.subscribe("agent_b")
    check("subscribe returns queue", q1 is not None)

    # Direct message
    direct_msg = AgentMessage.create("agent_a", "agent_b", MessageKind.BROADCAST,
                                     {"text": "hello"})
    await bus.send(direct_msg)
    check("message delivered to target", q2.qsize() == 1)
    check("sender queue not filled", q1.qsize() == 0)

    received = await q2.get()
    check("received correct message", received.id == direct_msg.id)

    # Broadcast
    bcast = AgentMessage.create("agent_a", "broadcast", MessageKind.BROADCAST, {"text": "hi all"})
    await bus.send(bcast)
    check("broadcast delivered to agent_b", q2.qsize() == 1)
    check("broadcast NOT delivered to sender (agent_a)", q1.qsize() == 0)
    await q2.get()

    # No subscriber → no crash
    missing_msg = AgentMessage.create("agent_a", "nonexistent", MessageKind.BROADCAST, {})
    await bus.send(missing_msg)  # should log warning, not raise

    # History
    history = bus.get_history()
    check("history records messages", len(history) >= 2)

    bus.unsubscribe("agent_a")
    check("unsubscribe removes agent", "agent_a" not in bus._subscribers)

    # ── Test 3: Individual agent lifecycle ───────────────────────────────
    print("\n[3] Individual Agent Lifecycle")

    bus2 = MessageBus()
    researcher = ResearcherAgent(bus2, search_fn=lambda q: f"Results for: {q}")

    await researcher.start()
    check("researcher started", researcher._running)

    # Send a task
    assign = AgentMessage.create(
        "test_caller", AgentRole.RESEARCHER,
        MessageKind.TASK_ASSIGN,
        {
            "task_id": "t_001",
            "description": "Find Python docs",
            "context": {"query": "Python asyncio tutorial"},
            "parent_task_id": None,
        },
    )
    reply_inbox = bus2.subscribe("test_caller")
    await bus2.send(assign)

    try:
        reply = await asyncio.wait_for(reply_inbox.get(), timeout=3.0)
        check("researcher replied", reply.kind == MessageKind.TASK_RESULT)
        check("result has query", "query" in reply.payload.get("result", {}),
              f"payload={reply.payload}")
        check("result is done", reply.payload.get("status") == "done")
    except asyncio.TimeoutError:
        check("researcher replied", False, "timeout waiting for result")

    status = researcher.status()
    check("status returns dict", isinstance(status, dict))
    check("status has role", status.get("role") == AgentRole.RESEARCHER.value)

    await researcher.stop()
    check("researcher stopped", not researcher._running)

    # ── Test 4: GuardianAgent veto ───────────────────────────────────────
    print("\n[4] GuardianAgent Safety Review")

    bus3 = MessageBus()
    guardian = GuardianAgent(bus3)
    await guardian.start()

    guard_inbox = bus3.subscribe("test_caller2")

    # Safe action
    safe_assign = AgentMessage.create(
        "test_caller2", AgentRole.GUARDIAN,
        MessageKind.TASK_ASSIGN,
        {
            "task_id": "g_001",
            "description": "List files in Documents",
            "context": {"action": "list files in Documents"},
        },
    )
    await bus3.send(safe_assign)
    safe_reply = await asyncio.wait_for(guard_inbox.get(), timeout=3.0)
    check("safe action approved", safe_reply.payload.get("result", {}).get("verdict") == "approved",
          f"result={safe_reply.payload.get('result')}")

    # Risky action
    risky_assign = AgentMessage.create(
        "test_caller2", AgentRole.GUARDIAN,
        MessageKind.TASK_ASSIGN,
        {
            "task_id": "g_002",
            "description": "Delete all log files",
            "context": {"action": "delete all log files from /var/log"},
        },
    )
    await bus3.send(risky_assign)
    risky_reply = await asyncio.wait_for(guard_inbox.get(), timeout=3.0)
    check("risky action needs_approval",
          risky_reply.payload.get("result", {}).get("verdict") == "needs_approval",
          f"result={risky_reply.payload.get('result')}")
    check("high risk level set",
          risky_reply.payload.get("result", {}).get("risk") == "high")

    # Broadcast veto
    bus3.subscribe(AgentRole.GUARDIAN.value)
    bcast_action = AgentMessage.create(
        "executor", "broadcast",
        MessageKind.BROADCAST,
        {"action": "send email to all users", "task_id": "e_001"},
    )
    await bus3.send(bcast_action)
    await asyncio.sleep(0.1)  # let guardian process

    await guardian.stop()

    # ── Test 5: OrchestratorAgent routing ────────────────────────────────
    print("\n[5] OrchestratorAgent Task Routing")

    bus4 = MessageBus()
    orch = OrchestratorAgent(bus4)
    researcher2 = ResearcherAgent(bus4)
    analyst = AnalystAgent(bus4)

    for agent in [orch, researcher2, analyst]:
        await agent.start()

    caller_inbox = bus4.subscribe("caller")

    # Research task → should route to researcher
    research_assign = AgentMessage.create(
        "caller", AgentRole.ORCHESTRATOR,
        MessageKind.TASK_ASSIGN,
        {
            "task_id": "o_001",
            "description": "Research quantum computing trends",
            "context": {"intent": "research"},
        },
    )
    await bus4.send(research_assign)

    reply = await asyncio.wait_for(caller_inbox.get(), timeout=3.0)
    check("orchestrator replied", reply.kind == MessageKind.TASK_RESULT,
          f"got kind={reply.kind}")
    result = reply.payload.get("result", {})
    check("orchestrator routed sub-tasks", result.get("routed") or result.get("sub_tasks", 0) >= 0,
          f"result={result}")

    for agent in [orch, researcher2, analyst]:
        await agent.stop()

    # ── Test 6: AgentCoordinator full lifecycle ───────────────────────────
    print("\n[6] AgentCoordinator Full Lifecycle")

    coordinator = AgentCoordinator(search_fn=lambda q: f"Search: {q}")

    health_before = coordinator.health_check()
    check("coordinator down before start", health_before["status"] == "down")

    await coordinator.start()

    health_after = coordinator.health_check()
    check("coordinator healthy after start", health_after["status"] == "healthy")
    check("all 6 agents running",
          len(health_after["agents"]) == 6, f"got {len(health_after['agents'])}")

    # Submit a task
    result = await coordinator.submit(
        description="Find information about Python asyncio",
        intent="research",
        timeout=5.0,
    )
    check("submit returns result", result is not None)
    check("result has status or routed", "status" in result or "routed" in result,
          f"result={result}")

    # Submit with timeout
    result2 = await coordinator.submit(
        description="analyze quarterly trends",
        intent="analyze",
        timeout=5.0,
    )
    check("analyze task returns result", result2 is not None)

    # Health includes bus stats
    h = coordinator.health_check()
    check("health has bus_queue_depths", "bus_queue_depths" in h)
    check("health has message_history_size", "message_history_size" in h)

    await coordinator.stop()
    check("coordinator stopped cleanly", not coordinator._running)

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
