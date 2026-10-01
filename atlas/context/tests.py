"""Query rewrite tests (Phase 1 Step E).

Run: python3 -m atlas.context.tests
No API keys. No external services — uses fake LLMQueue/ModelRouter stand-ins.
"""

from __future__ import annotations

import asyncio
import sys

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


class _FakeLLMQueue:
    def __init__(self, raise_error: bool = False) -> None:
        self._raise_error = raise_error

    async def run_job(self, job_fn, lane=None, label=None):
        if self._raise_error:
            raise RuntimeError("simulated background lane failure")
        return await job_fn()


class _FakeModelRouter:
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate(self, messages, tool_defs, system_prompt):
        from atlas.core.model_router import LLMResponse
        return LLMResponse(text=self._text)


async def run_tests() -> None:
    print("=" * 60)
    print("Query Rewrite Test Suite")
    print("=" * 60)

    from atlas.context.query_rewrite import rewrite_query

    # ── Test 1: an already-standalone query is never touched ───────────────
    print("\n[1] Standalone queries pass through verbatim")

    standalone = "What time is my dentist appointment?"
    result = await rewrite_query(standalone, recent_turns=[])
    check("standalone query with no recent turns is unchanged", result == standalone)

    result2 = await rewrite_query(
        standalone, recent_turns=[{"role": "user", "content": "Tell me about Priya"}],
    )
    check("standalone query is unchanged even with recent turns present", result2 == standalone)

    # ── Test 2: referential query, no prior turn, no model — can't resolve ──
    print("\n[2] Referential query with nothing to anchor to stays verbatim")

    referential = "What about it?"
    result3 = await rewrite_query(referential, recent_turns=[])
    check("referential query with no prior turns and no model falls back to verbatim",
          result3 == referential, f"got {result3!r}")

    # ── Test 3: T0 fallback — referential query + a clear prior turn, no model ──
    print("\n[3] T0 regex rewrite (clear antecedent, no model available)")

    recent = [
        {"role": "user", "content": "My dentist appointment is Thursday at 3pm."},
        {"role": "model", "content": "Got it."},
    ]
    result4 = await rewrite_query("What time is that?", recent_turns=recent)
    check("T0 folds the preceding user turn into the query", "dentist appointment is Thursday at 3pm" in result4,
          f"got {result4!r}")
    check("T0 rewrite still contains the original query text", "What time is that?" in result4, f"got {result4!r}")

    # ── Test 4: LLM rewrite preferred when a router is available ────────────
    print("\n[4] LLM rewrite is tried first when a model is available")

    llm_rewritten = "What time is the dentist appointment on Thursday?"
    result5 = await rewrite_query(
        "What time is that?", recent_turns=recent,
        llm_queue=_FakeLLMQueue(), model_router=_FakeModelRouter(llm_rewritten),
    )
    check("LLM rewrite result is used verbatim, not the T0 concatenation",
          result5 == llm_rewritten, f"got {result5!r}")

    # ── Test 5: LLM failure degrades to T0, doesn't raise ───────────────────
    print("\n[5] A failing LLMQueue degrades to the T0 rewrite instead of raising")

    result6 = await rewrite_query(
        "What time is that?", recent_turns=recent,
        llm_queue=_FakeLLMQueue(raise_error=True), model_router=_FakeModelRouter("unused"),
    )
    check("failure in the LLM path falls back to T0, no exception propagates",
          "dentist appointment is Thursday at 3pm" in result6, f"got {result6!r}")

    # ── Test 6: empty LLM response also degrades to T0 ──────────────────────
    print("\n[6] An empty/whitespace LLM response also falls back to T0")

    result7 = await rewrite_query(
        "What time is that?", recent_turns=recent,
        llm_queue=_FakeLLMQueue(), model_router=_FakeModelRouter("   "),
    )
    check("blank LLM output falls back to T0 rather than being used as-is",
          "dentist appointment is Thursday at 3pm" in result7, f"got {result7!r}")

    # ── Test 7: a long message isn't treated as referential just because it
    # contains one of the trigger words somewhere in it ─────────────────────
    print("\n[7] Long messages aren't misclassified as referential")

    long_msg = (
        "I wanted to let you know that it looks like the deployment pipeline "
        "finished successfully this morning and all the tests passed cleanly."
    )
    result8 = await rewrite_query(long_msg, recent_turns=recent)
    check("a long message containing 'it' incidentally is left untouched", result8 == long_msg)

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
