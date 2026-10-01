# ATLAS v1 — Direction & Redirection

> Audience: the Claude Code agent working in this repo.
> Read this whole file before changing anything. Work phase by phase. Each phase has exit criteria — do not start the next phase until the current one passes them.

---

## 0. The honest diagnosis

ATLAS has a strong skeleton: a real trust layer, a taint model, SQLite stores, an audit log, a failover router, a Tier-0 regex fast path. That is more than most "Jarvis" repos have.

But **the intelligence is not wired in**. The README describes 8 systems that cooperate; the code shows a single Gemini Flash tool loop with most systems built alongside it, not inside it. Verified from the source:

| README claim | What the code actually does |
|---|---|
| 4-tier RAG feeds every answer | `Engine._process_llm` never calls `RAGRetriever`. The executor sees chat history + world-state summary only. |
| World model knows your life | `world_model` only receives events (`record_event`). Nothing reads it into the prompt. Entity extraction is regex/keyword (`world/extractor.py`), no model. |
| 6 specialist AI agents | `agents/roles.py` routes on `if "research" in q`. No agent calls an LLM. `AgentCoordinator.submit` is never called from the request path. |
| Long-horizon planning | `planning/inference.py` is fixed templates picked by keyword. Every "learning" goal gets the same 5 tasks. |
| Self-improvement | Counts strings like "wrong"/"perfect". Recommendations are canned text. Nothing changes behaviour. |
| Proactive intelligence | Polling loops (30s, 0.5s perception) + AppleScript sources. |

**Intelligence comes from three things, in this order:**
1. **The model** doing the reasoning (Gemini 2.5 Flash is a fast, cheap model — it is the ceiling right now).
2. **The context** it gets each turn (who you are, what you're doing, what you've said before, what's pending).
3. **The tools and loop** that let it act, check its work, and keep going.

Heuristic subsystems that sit beside the model do not add intelligence. The redirection is therefore: **one excellent agent loop, fed by a context engine that actually uses the memory systems, routed across model tiers for latency, with everything else becoming tools or background jobs of that loop.**

---

## 1. Bugs to fix first (Phase 0)

Fix these before any feature work. Each one either crashes, silently does the wrong thing, or poisons later work.

1. **`RAGRetriever` constructor mismatch.** `orchestrator.py` does `RAGRetriever(config.db_path)` but `RAGRetriever.__init__` expects a `MemoryStore` and calls `memory_store.db.execute(...)`. A `Path` has no `.db` → `AttributeError` at startup. Pass the `MemoryStore` (and the `WorldModel`).
2. **`LLMQueue` caches actions.** The cache key is the query text and the default TTL is 30s for everything. Saying "volume up" twice within 30s returns the cached reply and **does not execute the tool again**. Same for "send it", "next track", etc. Fix: cache only read-only answers (no tool calls with tier ≠ AUTO in the trace), or remove the response cache entirely and keep only in-flight dedup.
3. **`LLMQueue` is serial.** One LLM call at a time means a background proactive evaluation blocks the user's request. Replace with a priority scheduler with separate lanes: `interactive` (never waits behind background) and `background` (concurrency 1–2, preemptible).
4. **Tool-call ID mapping in `_to_oai_messages`.** `last_call_ids[name]` is keyed by tool name, so two parallel calls to the same tool (e.g. two `read_file`) get the same ID → Groq/Ollama receive mismatched tool results. Key by call index / provider ID, and carry `ToolCall._id` through the internal message format.
5. **Loop detection is too blunt.** Any identical `(name, args)` call ends the whole task. Re-reading the screen or re-running tests after a fix is legitimate. Allow a repeat when state has changed in between (a write/act tool ran), and only stop on the third identical call with no intervening action.
6. **`max_rounds = 10`.** Too low for real multi-step work. Make it budget-driven (tokens/time/tool calls), default ~40 for interactive, higher for background tasks.
7. **Tool results truncated at 4000 chars** blindly. Replace with structure-aware compaction (keep head + tail, or write full result to a scratch file and give the model the path + a summary).
8. **Repo hygiene.** Remove `.DS_Store`; add it to `.gitignore`. `SOUR.md` is a pasted terminal transcript that includes an account email — remove it from the repo (and history if you care about it). Make the README describe what actually exists after each phase.

**Exit criteria:** app starts clean with `ATLAS_GEMINI_API_KEY` set; a regression test exists for each of bugs 1–6; all existing suites still pass.

---

## 2. Target architecture

```
                  ┌───────────────────────────────────────────┐
  voice / CLI ──▶ │  INTERFACE  (streaming in, streaming out) │
                  └──────────────────┬────────────────────────┘
                                     ▼
                  ┌───────────────────────────────────────────┐
                  │  ROUTER  (≤ 50 ms)                        │
                  │  T0 regex → T1 local small model intent   │
                  │  decides: direct tool | fast | deep       │
                  └──────┬───────────────┬───────────────┬────┘
                         ▼               ▼               ▼
                    direct tool     FAST lane        DEEP lane
                    (no LLM)        small/fast       frontier model
                                    cloud model      + extended thinking
                         └───────────────┬───────────────┘
                                         ▼
                  ┌───────────────────────────────────────────┐
                  │  AGENT LOOP  (one loop, tool use, verify) │◀──┐
                  │  tools: macOS, fs, shell, web, MCP,       │   │
                  │  memory.search, memory.write, plan.*,     │   │
                  │  spawn_subagent, schedule_task            │   │
                  └──────┬────────────────────────────────────┘   │
                         ▼                                        │
                  ┌──────────────┐   every call passes            │
                  │ TRUST LAYER  │   (keep it — it's good)        │
                  └──────────────┘                                │
                                                                  │
  ┌─────────────── CONTEXT ENGINE (assembled every turn) ─────────┘
  │  stable prefix (cached):  persona · tool defs · user profile
  │  semi-stable:             active goals · commitments · people
  │  per-turn:                retrieved memories (RAG) · screen state
  │                           · recent actions · time/calendar now
  └──────────────────────────────────────────────────────────────

  BACKGROUND (event-driven, background lane, never blocks interactive):
    memory extraction after every turn → world model + memories
    nightly reflection/consolidation
    proactive evaluator (cheap model judges whether to interrupt)
    long-running task runner (checkpointed, resumable)
```

Key principles:
- **One brain.** Delete the keyword multi-agent system as a request path. "Agents" become a `spawn_subagent(task, tools, model)` tool the main loop can call for parallel research or isolated long jobs.
- **Systems become tools or context, never parallel decision-makers.** Planning → `plan.create/update/list` tools whose content the *model* writes. World model → context + `memory.*` tools. Proactive engine → a background job that asks a cheap model "should I interrupt for this?".
- **Model-agnostic provider layer.** Keep the router, but make it support Anthropic, Gemini, Groq/OpenAI-compatible and local (Ollama or MLX) with a native message format that preserves tool-call IDs. Model names live in config, never in code.

---

## 3. Phases

### Phase 1 — Wire the brain to the memory (biggest intelligence gain)

1. **Context engine** (`atlas/context/engine.py`): `build(turn) -> ContextPack` returning ordered blocks with token counts.
   - Stable prefix: system prompt, tool definitions, user profile facts. Byte-identical across turns so provider prompt caching hits.
   - Per-turn: top-k from `RAGRetriever.retrieve(query)`, relevant world-model entities (people/projects named or implied in the query), active goals + due commitments, world-state delta, last N actions.
   - Respect `ContextBudgetManager` (already exists — use it).
2. **Memory write path:** after each completed turn, enqueue a background job that sends the turn to a cheap model with a strict JSON schema: `{facts[], people[], commitments[], preferences[], corrections[]}`. Write results into `world.db` / memories. Keep regex extractor only as a fallback.
3. **Memory tools for the model:** `memory_search(query)`, `memory_write(fact, type)`, `memory_forget(id)`. The model decides when to look things up mid-task.
4. **Vector search speed:** replace pure-Python cosine over every row with either `sqlite-vec` or a NumPy matrix held in RAM (normalised float32, one matmul). Target < 10 ms for 50k memories.
5. **Fix the eval loop before tuning** — see Phase 6; build at least the memory scenarios now.

Exit: asking "what did I tell you about X last week?" in a fresh session answers correctly; RAG p95 < 30 ms at 50k rows; memory scenarios in the eval suite pass.

### Phase 2 — Latency architecture

Budgets (measure, log, and show in `/status`):

| Path | Target p50 | Target p95 |
|---|---|---|
| T0 regex → direct tool ("volume 40") | 80 ms | 150 ms |
| T1 local intent → direct tool | 250 ms | 400 ms |
| Fast lane, first token | 400 ms | 800 ms |
| Voice in → first audio out, simple request | 700 ms | 1.2 s |
| Deep lane, first visible progress | 1.5 s | 3 s |

How:
1. **T1 router:** a small local model (via Ollama or MLX) with a constrained output schema `{lane, tool?, args?}`. Falls back to fast lane on low confidence. Keep T0 regex in front of it.
2. **Stream everything.** Executor yields tokens as they arrive; CLI renders them; TTS speaks sentence-by-sentence as they complete.
3. **Prompt caching.** Keep the stable prefix byte-identical; put per-turn context after it.
4. **Speculative work.** While the user is still speaking (streaming STT partials), prefetch: RAG retrieval, world-model lookup, screen state. Discard if the final transcript differs a lot.
5. **Kill subprocess cost.** AppleScript/`osascript` calls take hundreds of ms each. Move hot paths to PyObjC: EventKit for calendar/reminders, NSWorkspace for apps, Accessibility API for window/UI state, CoreAudio for volume. Keep a persistent JXA/AppleScript runner only for what has no native API.
6. **Persistent connections:** reuse HTTP/2 clients per provider; warm them at startup.
7. **Parallel tool execution** already exists — keep it, add per-tool timeouts (default 30s, from the audit plan).

Exit: latency table measured by an automated benchmark script and committed; all p95 targets met on an M-series Mac.

### Phase 3 — Voice that feels like JARVIS

1. Always-on wake word (local, e.g. openWakeWord) + VAD.
2. Streaming STT (faster-whisper with partial results, or a streaming cloud STT behind a config switch).
3. Streaming TTS with sentence chunking; **barge-in**: when the user speaks, stop TTS immediately and cancel the current generation via `cancel_token`.
4. Short acknowledgements spoken instantly from the router ("On it.") while the deep lane works; progress narration for tasks > 5 s.
5. Persona: keep the terse JARVIS style, but let the model be warm and witty in conversation, not only "Done."

Exit: you can interrupt it mid-sentence; voice-to-voice latency targets met.

### Phase 4 — Real planning and long-running autonomy

1. Replace template planning: `plan.create` is a tool; the **model** writes the goal breakdown, estimates, and dependencies. Store in `planning.db` (schema can stay).
2. **Task runner:** background tasks run the same agent loop with a larger budget, write checkpoints after every tool round (`TaskState` already has `last_checkpoint` — persist it), survive restarts, and report via notifications.
3. **Verification step:** before marking a task done, the loop must check its own work (re-read the file, run the tests, re-query the API). Add this to the system prompt and enforce it for background tasks.
4. **Scheduling:** `schedule_task(when, instruction)` tool backed by the existing scheduler.
5. Weekly replanner becomes: model reviews goals, progress and calendar, proposes the week, asks for approval.

Exit: "research the top 5 vector DBs, benchmark two locally, and write me a report" runs unattended for 20+ minutes, survives a restart mid-way, and finishes with a verified report.

### Phase 5 — Reach: tools, integrations, computer use

1. **MCP client.** Let ATLAS load MCP servers from config. This gives Gmail, Calendar, GitHub, Slack, Notion, filesystem, etc. without hand-writing integrations. Wrap every MCP tool in the trust layer with a tier and taint source.
2. **Screen understanding:** combine the Accessibility tree (fast, structured) with screenshots only when needed. Provide `ui_click(element_id)` style tools from the AX tree before falling back to coordinates.
3. Keep iMessage/Health `_local_only` enforcement — and add a test proving tainted local-only content cannot appear in any outbound request body.

### Phase 6 — Make "self-improvement" real

1. **Eval harness** (`evals/`): 50–100 scenario files (input, setup state, expected tool calls or checkable outcome). Runner reports success rate, p50/p95 latency, tokens, cost. Run in CI on every PR with a fake/cheap model for logic, and nightly with real models.
2. **Learned preferences:** when the user corrects ATLAS, the memory extractor records the correction as a preference that is injected into the stable prefix (e.g. "Prefers bullet summaries", "Calls the deploy repo 'infra'").
3. **Weekly reflection:** a background job reviews failures in the audit log + eval results and writes a short report of concrete changes it proposes (prompt edits, new tool, route rule). The human approves; nothing self-modifies code unattended.

Exit: eval score tracked over time in `evals/results/`; a correction made today changes behaviour tomorrow in a new session.

### Phase 7 — Safety for an agent this powerful

Keep and extend the trust layer:
- Irreversible or outward-facing actions (send, delete, pay, publish, push to main) always CONFIRM, in every mode, when the request chain includes EXTERNAL taint.
- Autonomous mode gets an allowlist of action classes, a daily spend cap, and a kill switch (`/pause` everything, including background tasks).
- Prompt-injection tests: emails/web pages containing instructions must not cause tool calls beyond reading.
- Secrets never enter the model context; `redact.py` runs on every outbound prompt, not just logs.

---

## 4. What to delete or demote

- `agents/roles.py` keyword routing → delete as a request path; replace with `spawn_subagent` tool.
- `planning/inference.py` templates → keep only as a fallback when offline.
- `improvement/analyzer.py` canned recommendations → replace with the eval/reflection loop.
- Duplicate autonomy systems (`autonomy/` simple sources + `AutonomyLoop` + `proactive/`) → merge into one event-driven proactive service.
- README marketing that doesn't match code → rewrite at the end of each phase.

## 5. Working rules for the agent

- Read the files involved before editing. Small, reviewable commits per item; one PR per phase.
- Every bug fix gets a regression test; every phase adds eval scenarios.
- Measure latency before and after any latency work; commit the numbers.
- No model names hard-coded outside config.
- Never weaken the trust layer to make a feature work.
- If a phase reveals the plan is wrong, write the reason in `docs/decisions/NNN-title.md` and adjust — don't silently diverge.
