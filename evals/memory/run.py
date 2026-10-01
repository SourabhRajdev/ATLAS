"""Phase 1 memory eval harness (Step C of PHASE_1_BRIEF.md).

Run: python3 -m evals.memory.run

Drives the REAL Orchestrator (CommandRouter -> Engine -> Executor -> ModelRouter)
against each scenario in evals/memory/scenarios/*.json, one fresh temp data_dir
per scenario (real MemoryStore/WorldModel SQLite stores, not mocks).

Needs a live LLM provider to produce the interesting baseline (ATLAS_GEMINI_API_KEY
/ ATLAS_GROQ_API_KEY env var, a macOS Keychain entry, or Ollama running at
localhost:11434). Without one — which is the case in CI and in this sandbox today —
every scenario still runs for real against a deterministic "abstaining" stand-in
model (see _AbstainingModelRouter) that always returns a neutral non-answer. This
exercises the whole real pipeline (routing, session handling, timestamp backdating,
assertion checking) and gives a genuine, reproducible baseline: the abstention and
injection scenarios are expected to PASS against it (a non-answer is, correctly,
non-hallucinating and non-instruction-following), while recall/supersession/
temporal/people/preference are expected to FAIL (there is nothing to recall from
a canned response) — which is exactly "mostly fail now" per Step C's exit check,
without needing network access or an API key to produce that signal honestly.

See docs/decisions/001-context-engine-and-memory.md for why most of these are
expected to fail even WITH a live provider today: RAG/world-model retrieval isn't
wired into the request path yet (Steps D-F fix that).

SAFETY NOTE for a real-provider run: this harness uses the real ToolRegistry with
no approval_callback wired up. CONFIRM-tier tool calls are auto-denied (Executor
denies by default with no callback), but AUTO-tier tools (open_app, show_notification,
control_volume, ...) execute for real if the live model decides to call one — this
is deliberate (an eval that fakes tool execution isn't evaluating the real agent),
but it means a real-provider run can visibly do things on the machine it runs on.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCENARIOS_DIR = Path(__file__).with_name("scenarios")
RESULTS_DIR = Path(__file__).parent.parent / "results"


def check(name: str, condition: bool, detail: str = "") -> None:
    # Printing only — the authoritative pass/fail/total counts are computed
    # in run_all() from the structured `results` list (used for the JSON
    # output too), not tallied here, so there's no separate counter to drift
    # out of sync with it.
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f" | {detail}" if detail else ""))


# --------------------------------------------------------------------------- #
# Deterministic stand-in model — used whenever no live provider is configured
# --------------------------------------------------------------------------- #

class _AbstainingModelRouter:
    """Matches ModelRouter.generate()'s signature. Always abstains, no tool calls.

    Lets the harness drive the real CommandRouter/Engine/Executor pipeline
    end-to-end without network access, producing a genuine (if deliberately
    unintelligent) baseline instead of silently skipping every scenario.
    """

    name = "fake-abstaining"

    async def generate(self, messages, tool_defs, system_prompt):
        from atlas.core.model_router import LLMResponse
        return LLMResponse(
            text="I don't have that information — nothing in what you've told me covers that.",
            provider=self.name,
        )

    def active_provider(self) -> str:
        return self.name


def _has_live_provider(config) -> bool:
    if config.gemini_api_key or config.groq_api_key:
        return True
    try:
        import httpx
        r = httpx.get(f"{config.ollama_base_url}/api/tags", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Scenario loading + expectation checking
# --------------------------------------------------------------------------- #

def _load_scenarios() -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted(SCENARIOS_DIR.glob("*.json"))
    ]


def _turn_text(turn) -> str:
    return turn if isinstance(turn, str) else turn["text"]


def _turn_expect(turn) -> dict | None:
    return None if isinstance(turn, str) else turn.get("expect")


def _check_expect(expect: dict, response_text: str) -> tuple[bool, str]:
    text_lower = response_text.lower()
    problems: list[str] = []

    for needle in expect.get("contains", []):
        if needle.lower() not in text_lower:
            problems.append(f"missing required text: {needle!r}")

    contains_any = expect.get("contains_any")
    if contains_any and not any(n.lower() in text_lower for n in contains_any):
        problems.append(f"none of {contains_any!r} found")

    for forbidden in expect.get("must_not_contain", []):
        if forbidden.lower() in text_lower:
            problems.append(f"contains forbidden text: {forbidden!r}")

    pattern = expect.get("matches_regex")
    if pattern and not re.search(pattern, response_text, re.MULTILINE):
        problems.append(f"does not match required pattern: {pattern!r}")

    return (not problems), "; ".join(problems)


# --------------------------------------------------------------------------- #
# Running a single scenario against the real Orchestrator
# --------------------------------------------------------------------------- #

async def run_scenario(scenario: dict, provider_available: bool) -> dict:
    name = scenario["name"]
    family = scenario.get("family", "unknown")

    from atlas.config import Settings
    from atlas.memory.store import MemoryStore
    from atlas.tools.registry import ToolRegistry
    from atlas.core.orchestrator import Orchestrator

    turn_records: list[dict] = []
    checks_passed = True

    with tempfile.TemporaryDirectory() as tmp:
        config = Settings(data_dir=Path(tmp))
        config.ensure_dirs()
        memory = MemoryStore(config.db_path)
        tools = ToolRegistry()
        orch = Orchestrator(config, memory, tools)

        if not provider_available:
            fake = _AbstainingModelRouter()
            orch.engine.model_router = fake
            orch.engine.executor.model_router = fake

        await orch.engine.llm_queue.start()
        try:
            for s_idx, session in enumerate(scenario["sessions"]):
                session_id = f"{name}-s{s_idx}"
                for turn in session.get("turns", []):
                    text = _turn_text(turn)
                    expect = _turn_expect(turn)
                    t0 = time.monotonic()
                    response, _trace = await orch.process(text, session_id)
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    record = {
                        "session": session_id,
                        "turn": text,
                        "response": response,
                        "elapsed_ms": round(elapsed_ms, 1),
                        "approx_tokens": max(1, len(response) // 4),
                    }
                    if expect is not None:
                        ok, detail = _check_expect(expect, response)
                        record["expect"] = expect
                        record["passed"] = ok
                        record["detail"] = detail
                        checks_passed = checks_passed and ok
                    turn_records.append(record)

                days_ago = session.get("days_ago")
                if days_ago:
                    memory.db.execute(
                        "UPDATE messages SET created_at = datetime(created_at, ?) "
                        "WHERE session_id = ?",
                        (f"-{days_ago} days", session_id),
                    )
                    memory.db.commit()
        finally:
            orch.engine.llm_queue.stop()
            memory.close()
            orch.world_model.close()
            orch.scheduler.close()
            orch.threads.close()
            orch.planning.close()
            orch.improvement.close()

    status = "PASS" if checks_passed else "FAIL"
    return {"name": name, "family": family, "status": status, "turns": turn_records}


# --------------------------------------------------------------------------- #

async def run_all() -> dict:
    from atlas.config import Settings

    probe_config = Settings()
    provider_available = _has_live_provider(probe_config)
    model_label = probe_config.model if provider_available else _AbstainingModelRouter.name

    print("=" * 60)
    print("Phase 1 Memory Eval Suite")
    print(f"model: {model_label}" + ("" if provider_available else "  (no live provider configured — see module docstring)"))
    print("=" * 60)

    scenarios = _load_scenarios()
    results: list[dict] = []

    for scenario in scenarios:
        print(f"\n[{scenario['family']}] {scenario['name']}")
        result = await run_scenario(scenario, provider_available)
        results.append(result)
        if result["status"] == "PASS":
            check(scenario["name"], True)
        else:
            failing = [t for t in result["turns"] if t.get("passed") is False]
            detail = "; ".join(t["detail"] for t in failing) if failing else "no checked turn passed"
            check(scenario["name"], False, detail)

    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = total - passed

    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "model": model_label,
        "provider_available": provider_available,
        "scenarios": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / total, 3) if total else None,
        },
    }

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} scenarios passed" + (f"  ({failed} FAILED)" if failed else "  (all pass)"))
    print("=" * 60)

    return summary


def _write_results(summary: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    model_tag = summary["model"].replace("/", "-")
    out_path = RESULTS_DIR / f"{date}-{model_tag}.json"
    out_path.write_text(json.dumps(summary, indent=2))
    return out_path


def main() -> None:
    summary = asyncio.run(run_all())
    out_path = _write_results(summary)
    print(f"\nResults written to {out_path.relative_to(Path(__file__).parent.parent.parent)}")
    # A fully-abstaining baseline run (no live provider) is expected to have
    # failures by design (see module docstring) — don't fail CI over that.
    # Only a genuine failure against a REAL provider is worth a non-zero exit.
    sys.exit(0 if (summary["summary"]["failed"] == 0 or not summary["provider_available"]) else 1)


if __name__ == "__main__":
    main()
