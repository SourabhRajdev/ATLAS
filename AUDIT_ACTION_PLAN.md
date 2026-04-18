# ATLAS — Post-Audit Action Plan

**Date**: April 18, 2026  
**Status**: Prototype → Production Roadmap

---

## IMMEDIATE FIXES (Do This Week)

### Critical Bugs (Will Break System)
- [x] **Fixed**: `atlas/core/approval.py` typo (`impoport` → `import`)
- [ ] **Add error handling in Orchestrator.start()** — Wrap perception daemon in try/except, auto-restart on crash
- [ ] **Add LRU cache eviction to LLMQueue** — Cap at 1000 entries, evict oldest
- [ ] **Add timeout to tool execution** — 30s default, configurable per tool
- [ ] **Add retry logic to autonomy sources** — 3x exponential backoff for osascript calls

### Quick Wins (High Impact, Low Effort)
- [ ] **Add `/undo` command** — Wire up existing UndoLog to CLI
- [ ] **Add progress indicators** — Stream progress events for long-running tools
- [ ] **Add `/tasks` command** — View/edit scheduled tasks
- [ ] **Lower autonomy kill switch threshold** — 5 outputs in 10 min (currently 3 in 5 min)
- [ ] **Add cost breakdown to `/cost`** — Per-tool, per-session, per-day

---

## PHASE 1: STABILIZATION (Weeks 1-2)

**Goal**: Make existing features reliable and production-ready.

### Memory System
- [ ] Lower deduplication threshold from 75% to 85% (too aggressive)
- [ ] Add user confirmation for memory merges
- [ ] Add memory expiry (auto-delete after 90 days unless accessed)
- [ ] Add `/memories delete <id>` command

### Autonomy System
- [ ] Wire up feedback system — Add inline accept/dismiss prompts
- [ ] Fix calendar date parsing — Use proper date format or switch to EventKit
- [ ] Add `/pause autonomy <minutes>` command
- [ ] Add autonomy dashboard to `/world` output

### Safety & Trust
- [ ] Add rate limiting — 60 LLM calls/min, 100 tool calls/min
- [ ] Add spend limits — Hard cap at $10/day (configurable)
- [ ] Add parameter validation — Pydantic schemas for all tools
- [ ] Add anomaly alerting — Notify on repeated failures, unusual patterns

### Infrastructure
- [ ] Add health check endpoint — HTTP server on :8080/health
- [ ] Add graceful shutdown — Signal handlers, drain queues
- [ ] Add daily backups — SQLite → `~/.atlas/backups/YYYY-MM-DD.db`
- [ ] Centralize paths — Move all `~/.atlas/` references to config

---

## PHASE 2: INTEGRATION (Weeks 3-6)

**Goal**: Add missing life integrations.

### Priority 1: Calendar (Week 3)
- [ ] Fix AppleScript date handling
- [ ] Add recurring events support
- [ ] Add attendees, reminders, timezone support
- [ ] Add `/calendar today|week|month` commands

### Priority 2: Gmail (Weeks 4-5)
- [ ] Implement Google OAuth flow
- [ ] Add Gmail API client (read, send, draft, archive)
- [ ] Add thread tracking and label management
- [ ] Add `/mail unread|today|search <query>` commands

### Priority 3: Obsidian (Week 6)
- [ ] Wire up FilesSource to watch vault directory
- [ ] Add markdown parsing (frontmatter, backlinks)
- [ ] Add `/notes search|create|update` commands
- [ ] Add daily note integration

### Deferred (Phase 3)
- **iMessage**: Requires private APIs (risky), defer to Phase 3
- **Apple Health**: Requires HealthKit integration, defer to Phase 3
- **Spotify**: Lower priority, defer to Phase 3

---

## PHASE 3: INTELLIGENCE (Weeks 7-10)

**Goal**: Make ATLAS truly proactive and intelligent.

### Multi-Agent Coordination
- [ ] Design agent roles (Planner, Executor, Verifier, Memory Manager)
- [ ] Implement agent communication protocol
- [ ] Add agent handoff logic
- [ ] Add agent performance tracking

### Long-Horizon Planning
- [ ] Implement task decomposition (goal → subtasks)
- [ ] Add task dependency tracking
- [ ] Add task progress monitoring
- [ ] Add task resumption after interruption

### Self-Improvement Loop
- [ ] Track tool success/failure rates
- [ ] Track user corrections ("no, the other one")
- [ ] Adjust confidence thresholds based on feedback
- [ ] Generate synthetic training data from corrections

### Continuity System
- [ ] Wire ThreadManager into orchestrator
- [ ] Add thread surfacing logic (cooldown, priority)
- [ ] Add `/threads list|resume|complete` commands
- [ ] Add automatic thread creation for multi-turn tasks

---

## PHASE 4: POLISH (Weeks 11-12)

**Goal**: Production-ready UX and monitoring.

### Voice Interface
- [ ] Add wake word detection (Porcupine)
- [ ] Add speaker diarization
- [ ] Add ambient mode (continuous listening)
- [ ] Improve voice response compression

### Monitoring & Observability
- [ ] Add Prometheus metrics export
- [ ] Add structured logging (JSON)
- [ ] Add distributed tracing (OpenTelemetry)
- [ ] Add error tracking (Sentry)

### Documentation
- [ ] Write user guide (setup, commands, workflows)
- [ ] Write developer guide (architecture, adding tools)
- [ ] Write deployment guide (systemd, Docker, cloud)
- [ ] Record demo videos

### Testing
- [ ] Add unit tests (tools, memory, autonomy)
- [ ] Add integration tests (end-to-end flows)
- [ ] Add performance tests (LLM queue, memory search)
- [ ] Add security tests (shell policy, path safety)

---

## METRICS FOR SUCCESS

### Reliability
- **Uptime**: >99% (measured over 7 days)
- **Error rate**: <1% of tool executions
- **Crash recovery**: Auto-restart within 5s

### Performance
- **Response time**: <2s for simple queries (p95)
- **LLM cache hit rate**: >40%
- **CommandRouter hit rate**: >50%

### Intelligence
- **Autonomy precision**: >80% (user accepts suggestions)
- **Memory recall**: >90% (finds relevant memories)
- **Task completion**: >85% (completes without errors)

### Cost
- **Daily spend**: <$1 per active user
- **Token efficiency**: >60% savings via cache+router
- **API failures**: <5% (fallback to Groq/Ollama)

---

## KNOWN LIMITATIONS (Accept for v1.0)

These are **not bugs** — they're conscious tradeoffs for v1.0:

1. **No web dashboard** — CLI only. Web UI is Phase 5.
2. **No mobile app** — Desktop only. Mobile is Phase 5.
3. **No plugin system** — Tools are hardcoded. Plugins are Phase 5.
4. **No OAuth for Gmail** — Manual token setup. OAuth is Phase 2.
5. **No iMessage read access** — Send-only via AppleScript. Private APIs are risky.
6. **No real-time collaboration** — Single-user only.
7. **No cloud sync** — Local SQLite only.
8. **No multi-language support** — English only.

---

## NEXT STEPS

1. **This week**: Fix critical bugs (Orchestrator error handling, LRU cache, timeouts)
2. **Week 1-2**: Stabilization (memory, autonomy, safety)
3. **Week 3**: Calendar integration
4. **Week 4-5**: Gmail integration
5. **Week 6**: Obsidian integration
6. **Week 7-10**: Multi-agent + long-horizon planning
7. **Week 11-12**: Polish + testing

---

## VERDICT SUMMARY

**Current State**: Well-architected prototype with 60-70% of plumbing in place.

**Strengths**:
- 3-tier routing (CommandRouter → LLMQueue → Gemini) is production-grade
- Multi-provider failover with cooldown is solid
- Memory deduplication and context compression are correct strategies
- Perception daemon with privacy firewall shows real safety thinking
- Shell policy with proper parsing is better than 90% of hobby projects

**Weaknesses**:
- Continuity system exists but not wired up
- Autonomy loop only acts on scheduled tasks (other signals just notify)
- No undo command despite undo log existing
- No feedback UI despite learning system existing
- Calendar date handling is broken
- No rate limiting or spend caps

**Bottom Line**: This is **not a demo**. It's a real system with real architecture. But it's **not production-ready** yet. The bones are excellent — you just need to finish wiring everything together and add the missing integrations.

**Estimated Time to Production**: 8-12 weeks of focused work.

**Recommendation**: Focus on Phase 1 (stabilization) first. Don't add new features until existing features are reliable. Then tackle integrations one at a time (Calendar → Gmail → Obsidian).
