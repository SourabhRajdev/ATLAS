"""Core system tests — Phase 0 regression tests (ATLAS_V1_DIRECTION.md bugs 1-6).

Run: python3 -m atlas.core.tests
No API keys. No external services. No macOS Keychain items required.
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


# --------------------------------------------------------------------------- #
# Fakes shared across the Executor-based tests (bugs 5, 6)
# --------------------------------------------------------------------------- #

class _FakeModelRouter:
    """Returns scripted LLMResponses in order, one per agent-loop round."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def generate(self, messages, tool_defs, system_prompt):
        self.calls += 1
        if not self._responses:
            from atlas.core.model_router import LLMResponse
            return LLMResponse(text="(no more scripted responses)")
        return self._responses.pop(0)


class _FakeToolRegistry:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def get_anthropic_tools(self):
        return []

    def get_tool(self, name: str):
        from atlas.core.models import ToolDef, Tier
        return ToolDef(name=name, description="", parameters={"type": "object", "properties": {}}, tier=Tier.AUTO)

    async def execute(self, name: str, params: dict):
        from atlas.core.models import ActionRecord
        self.executed.append((name, dict(params)))
        return ActionRecord(tool_name=name, params=params, result="ok", error=None)


class _FakeMemory:
    def log_action(self, record) -> None:
        pass


async def _run_executor(responses: list):
    from atlas.core.executor import Executor
    from atlas.core.models import Budget

    router = _FakeModelRouter(responses)
    tools = _FakeToolRegistry()
    memory = _FakeMemory()
    executor = Executor(model_router=router, config=None, tools=tools, memory=memory, trust=None)

    budget = Budget(max_rounds=len(responses) + 2, max_tool_calls=1000, max_ms=30_000)
    events = []
    async for ev in executor.run("do things", "session-1", budget=budget):
        events.append(ev)
    return events, tools


def _tool_call_response(name: str, args: dict):
    from atlas.core.model_router import LLMResponse, ToolCall
    return LLMResponse(tool_calls=[ToolCall(name=name, args=args)])


def _done_response(text: str = "done"):
    from atlas.core.model_router import LLMResponse
    return LLMResponse(text=text)


# --------------------------------------------------------------------------- #
# Bug 1: RAGRetriever / IngestionPipeline constructor mismatch
# --------------------------------------------------------------------------- #

async def test_bug1_rag_constructor() -> None:
    print("\n[1] RAGRetriever/IngestionPipeline constructor mismatch (orchestrator.py)")

    src = Path(__file__).with_name("orchestrator.py").read_text()
    check(
        "orchestrator no longer passes a bare Path to RAGRetriever",
        "RAGRetriever(config.db_path)" not in src,
    )
    check(
        "orchestrator no longer passes a bare Path to IngestionPipeline",
        "IngestionPipeline(config.db_path)" not in src,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        from atlas.memory.store import MemoryStore
        from atlas.rag.retriever import RAGRetriever
        from atlas.rag.ingestion import IngestionPipeline
        from atlas.world.world_model import WorldModel

        mem = MemoryStore(Path(tmpdir) / "atlas.db")
        world = WorldModel(Path(tmpdir) / "world.db")

        try:
            retriever = RAGRetriever(mem, world)
            ok = True
        except AttributeError as e:
            ok = False
            retriever = None
            check("RAGRetriever(memory, world) constructs without AttributeError", ok, str(e))
        else:
            check("RAGRetriever(memory, world) constructs without AttributeError", ok)

        if retriever is not None:
            results = await retriever.retrieve("anything", limit=5)
            check("RAGRetriever.retrieve() runs end-to-end after the fix", isinstance(results, list))

        try:
            IngestionPipeline(mem, world)
            ok2 = True
        except AttributeError as e:
            ok2 = False
            check("IngestionPipeline(memory, world) constructs without AttributeError", ok2, str(e))
        else:
            check("IngestionPipeline(memory, world) constructs without AttributeError", ok2)

        mem.close()
        world.close()


# --------------------------------------------------------------------------- #
# Bug 2: LLMQueue caches tool-call side effects
# --------------------------------------------------------------------------- #

async def test_bug2_cache_skips_tool_calls() -> None:
    print("\n[2] LLMQueue does not cache responses that ran a tool")

    from atlas.core.llm_queue import LLMQueue
    from atlas.core.models import TaskState, Event, EventType

    call_count = {"n": 0}

    async def process_fn(query, session_id, world):
        call_count["n"] += 1
        trace = TaskState(goal=query, session_id=session_id)
        if query == "volume up":
            trace.observations.append(Event(
                type=EventType.TOOL_CALL, content={"name": "control_volume", "args": {"delta": 10}},
            ))
        return f"response #{call_count['n']}", trace

    queue = LLMQueue(process_fn=process_fn)
    await queue.start()
    try:
        r1 = await queue.enqueue("volume up", "s1")
        r2 = await queue.enqueue("volume up", "s1")
        check(
            "a tool-call-bearing response is not served from cache",
            r1[0] != r2[0] and call_count["n"] == 2,
            f"calls={call_count['n']}, r1={r1[0]!r}, r2={r2[0]!r}",
        )

        call_count["n"] = 0
        r3 = await queue.enqueue("what time is it", "s1")
        r4 = await queue.enqueue("what time is it", "s1")
        check(
            "a pure-answer response (no tool calls) is still cached",
            r3[0] == r4[0] and call_count["n"] == 1,
            f"calls={call_count['n']}, r3={r3[0]!r}, r4={r4[0]!r}",
        )
    finally:
        queue.stop()


# --------------------------------------------------------------------------- #
# Bug 3: LLMQueue is serial — background work blocks interactive requests
# --------------------------------------------------------------------------- #

async def test_bug3_interactive_lane_not_blocked() -> None:
    print("\n[3] Background lane work does not block the interactive lane")

    from atlas.core.llm_queue import LLMQueue, LANE_BACKGROUND, LANE_INTERACTIVE

    async def process_fn(query, session_id, world):
        if query == "slow background job":
            await asyncio.sleep(0.4)
        return f"handled: {query}", None

    queue = LLMQueue(process_fn=process_fn)
    await queue.start()
    try:
        bg_task = asyncio.create_task(
            queue.enqueue("slow background job", "s-bg", lane=LANE_BACKGROUND)
        )
        await asyncio.sleep(0.05)  # let the background worker pick it up

        start = time.monotonic()
        result = await queue.enqueue("urgent user question", "s-fg", lane=LANE_INTERACTIVE)
        elapsed = time.monotonic() - start

        check(
            "interactive request completes without waiting on the background job",
            elapsed < 0.2,
            f"elapsed={elapsed:.3f}s (background job takes 0.4s)",
        )
        check("interactive request still got the right answer", result[0] == "handled: urgent user question")

        await bg_task  # drain
    finally:
        queue.stop()


# --------------------------------------------------------------------------- #
# Bug 4: tool-call ID mapping for parallel same-name calls
# --------------------------------------------------------------------------- #

def test_bug4_parallel_tool_call_ids() -> None:
    print("\n[4] Tool-call IDs for parallel same-name calls (_to_oai_messages)")

    from atlas.core.model_router import _to_oai_messages

    messages = [
        {"role": "user", "parts": [{"text": "read two files"}]},
        {"role": "model", "parts": [
            {"function_call": {"name": "read_file", "args": {"path": "a.txt"}}},
            {"function_call": {"name": "read_file", "args": {"path": "b.txt"}}},
        ]},
        {"role": "user", "parts": [
            {"function_response": {"name": "read_file", "response": {"result": "contents of a"}}},
            {"function_response": {"name": "read_file", "response": {"result": "contents of b"}}},
        ]},
    ]

    oai = _to_oai_messages(messages, "system prompt")
    assistant_msgs = [m for m in oai if m["role"] == "assistant"]
    tool_msgs = [m for m in oai if m["role"] == "tool"]

    check("one assistant turn with two tool calls", len(assistant_msgs) == 1 and len(assistant_msgs[0]["tool_calls"]) == 2)
    ids = [tc["id"] for tc in assistant_msgs[0]["tool_calls"]]
    check("two parallel same-name calls get distinct IDs", len(set(ids)) == 2, f"ids={ids}")

    check("two tool result messages produced", len(tool_msgs) == 2)
    check(
        "first tool result references the first call's ID",
        tool_msgs[0]["tool_call_id"] == ids[0],
        f"{tool_msgs[0]['tool_call_id']!r} != {ids[0]!r}",
    )
    check(
        "second tool result references the second call's ID (not the first)",
        tool_msgs[1]["tool_call_id"] == ids[1],
        f"{tool_msgs[1]['tool_call_id']!r} != {ids[1]!r}",
    )
    check(
        "tool result content stays matched to the right call",
        tool_msgs[0]["content"] == "contents of a" and tool_msgs[1]["content"] == "contents of b",
    )


# --------------------------------------------------------------------------- #
# Bug 5: loop detection is too blunt
# --------------------------------------------------------------------------- #

async def test_bug5_loop_detection() -> None:
    print("\n[5] Loop detection allows legitimate repeats, blocks tight repeats")

    from atlas.core.models import EventType

    # Scenario A: A, B, A, B, A, then done — never 2 identical calls in a row.
    # Must NOT be flagged as a loop.
    responses_a = [
        _tool_call_response("check_status", {}),
        _tool_call_response("apply_fix", {"n": 1}),
        _tool_call_response("check_status", {}),
        _tool_call_response("apply_fix", {"n": 2}),
        _tool_call_response("check_status", {}),
        _done_response(),
    ]
    events_a, tools_a = await _run_executor(responses_a)
    loop_errors_a = [e for e in events_a if e.type == EventType.ERROR and "loop detected" in str(e.content)]
    check(
        "alternating repeats (A,B,A,B,A) are not flagged as a loop",
        len(loop_errors_a) == 0,
        f"errors={[e.content for e in loop_errors_a]}",
    )
    check("all 5 alternating calls executed", len(tools_a.executed) == 5, f"executed={tools_a.executed}")

    # Scenario B: the exact same call three times in a row, nothing in between.
    # Must be blocked at the 3rd attempt (only 2 actually execute).
    responses_b = [
        _tool_call_response("poll_status", {"id": 1}),
        _tool_call_response("poll_status", {"id": 1}),
        _tool_call_response("poll_status", {"id": 1}),
        _done_response(),
    ]
    events_b, tools_b = await _run_executor(responses_b)
    loop_errors_b = [e for e in events_b if e.type == EventType.ERROR and "loop detected" in str(e.content)]
    check("three identical consecutive calls are flagged as a loop", len(loop_errors_b) == 1)
    check(
        "the loop is stopped before the 3rd identical call executes",
        len(tools_b.executed) == 2,
        f"executed={tools_b.executed}",
    )


# --------------------------------------------------------------------------- #
# Bug 6: max_rounds = 10 hardcoded, too low for real multi-step work
# --------------------------------------------------------------------------- #

async def test_bug6_max_rounds_budget_driven() -> None:
    print("\n[6] max_rounds comes from Budget, not a hardcoded 10")

    from atlas.core.models import Budget
    from atlas.core.models import EventType

    check("interactive default budget allows more than 10 rounds", Budget().max_rounds > 10, f"got {Budget().max_rounds}")
    check(
        "background budget allows more rounds than interactive",
        Budget.for_background().max_rounds > Budget().max_rounds,
        f"background={Budget.for_background().max_rounds}, interactive={Budget().max_rounds}",
    )

    # 15 distinct tool calls (unique args each round) — would have hit the old
    # hardcoded max_rounds=10 ceiling and stopped with "(max rounds reached)".
    responses = [_tool_call_response("step", {"n": i}) for i in range(15)] + [_done_response()]
    events, tools = await _run_executor(responses)

    check("all 15 distinct-round tool calls executed", len(tools.executed) == 15, f"executed={len(tools.executed)}")
    max_rounds_hit = [e for e in events if e.type == EventType.DONE and "max rounds reached" in str(e.content)]
    check("did not hit '(max rounds reached)' at round 10", len(max_rounds_hit) == 0)


# --------------------------------------------------------------------------- #

async def run_tests() -> None:
    print("=" * 60)
    print("Core System Test Suite (Phase 0 regressions)")
    print("=" * 60)

    await test_bug1_rag_constructor()
    await test_bug2_cache_skips_tool_calls()
    await test_bug3_interactive_lane_not_blocked()
    test_bug4_parallel_tool_call_ids()
    await test_bug5_loop_detection()
    await test_bug6_max_rounds_budget_driven()

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
