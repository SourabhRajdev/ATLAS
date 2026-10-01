# CLAUDE.md — ATLAS

ATLAS is a local-first, always-on personal AI agent for macOS (Python 3.11+, asyncio, SQLite). The goal is a JARVIS-class assistant: deeply context-aware, proactive, fast, and safe.

## Source of truth
- **`ATLAS_V1_DIRECTION.md`** is the plan. Follow its phases in order and meet each phase's exit criteria before moving on.
- Current phase: **Phase 1 (wire the brain to the memory)**. Phase 0 (bug fixes) completed — see PR that introduced this file. Update this line when a phase completes.

## Architecture in one paragraph
User input → `CommandRouter` (Tier-0 regex) → `Engine` → `Executor` agent loop → `ModelRouter` (provider failover) → `ToolRegistry`, with every tool call gated by `TrustLayer` (hard limits, taint, consequence tiers, append-only audit). Memory lives in `MemoryStore` + `RAGRetriever` + `WorldModel`; the target design feeds these into every turn through a context engine. Background work (perception, proactive, scheduler) must never block the interactive lane.

## Commands
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
for mod in core trust world rag proactive integrations planning improvement agents; do
  python -m atlas.$mod.tests || exit 1
done
atlas   # run the CLI
```

## Rules
- Read before you edit. Keep commits small; one PR per phase.
- Every bug fix ships with a regression test. Every phase adds eval scenarios under `evals/`.
- Latency work: measure before and after, commit the numbers.
- Model names and provider choices live in config only.
- Never weaken or bypass the trust layer, taint tracking, or `_local_only` enforcement to make something work.
- Background jobs use the background LLM lane; the interactive lane is never queued behind them.
- Keep the README honest — describe what the code does, not what it will do.
- Record any deviation from the plan in `docs/decisions/NNN-title.md`.
