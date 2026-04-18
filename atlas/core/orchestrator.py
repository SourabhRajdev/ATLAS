"""Orchestrator — ATLAS central nervous system.

Wires together:
  - PerceptionDaemon (what's happening on screen)
  - Engine / Executor (LLM reasoning + tool use)
  - ActionRouter (AppleScript / Playwright / AX)
  - Autonomy sources + Reasoner + Budget  (simple real-time signals)
  - AutonomyLoop                          (scheduler + memory + LLM-evaluated signals)
  - MemoryStore (FTS + semantic + snapshots + feedback)
  - SignalLearner (EMA feedback loop)
  - ThreadManager (continuity across sessions)
  - Scheduler (background tasks)

Lifecycle: init() -> start() -> [process() / stop()]
"""

from __future__ import annotations

import asyncio
import logging
import time

from atlas.agents.coordinator import AgentCoordinator
from atlas.autonomy.budget import NotificationBudget
from atlas.autonomy.learning import SignalLearner
from atlas.autonomy.loop import AutonomyLoop
from atlas.autonomy.models import Priority, Signal
from atlas.autonomy.reasoner import Reasoner
from atlas.autonomy.sources import (
    BatterySource,
    CalendarSource,
    ClipboardSource,
    FilesSource,
    GitSource,
    MailSource,
)
from atlas.config import Settings
from atlas.continuity.threads import ThreadManager
from atlas.control.models import Action, Capability
from atlas.control.router import ActionRouter
from atlas.core.command_router import CommandRouter
from atlas.core.engine import Engine
from atlas.core.models import Event, EventType, TaskState
from atlas.improvement.engine import SelfImprovementEngine
from atlas.integrations.manager import IntegrationManager
from atlas.memory.store import MemoryStore
from atlas.perception.daemon import PerceptionDaemon
from atlas.planning.engine import PlanningEngine
from atlas.proactive.engine import ProactiveEngine
from atlas.proactive.signals import Signal as ProactiveSignal, SignalType
from atlas.rag.ingestion import IngestionPipeline
from atlas.rag.retriever import RAGRetriever
from atlas.scheduler.scheduler import Scheduler
from atlas.tools.registry import ToolRegistry
from atlas.world.world_model import WorldModel

logger = logging.getLogger("atlas.orchestrator")

AUTONOMY_POLL_S = 30
SNAPSHOT_INTERVAL_S = 60


class Orchestrator:
    def __init__(self, config: Settings, memory: MemoryStore, tools: ToolRegistry) -> None:
        self.config = config
        self.memory = memory

        # Core
        self.engine = Engine(config, memory, tools)
        self.router = ActionRouter(confirm_fn=self._default_confirm)
        self.command_router = CommandRouter()   # Tier-0: zero-LLM fast path
        self._tools = tools                     # needed for direct tool execution

        # Perception
        self.perception = PerceptionDaemon()

        # Scheduler
        self.scheduler = Scheduler(config.data_dir / "scheduler.db")

        # Continuity
        self.threads = ThreadManager(config.data_dir / "threads.db")

        # Simple real-time autonomy (Calendar, Mail, Git, Battery, Clipboard)
        self.learner = SignalLearner()
        self.reasoner = Reasoner(self.learner)
        self.budget = NotificationBudget()
        self._sources = [
            CalendarSource(),
            MailSource(),
            GitSource(),
            BatterySource(),
            ClipboardSource(),
        ]
        self._files_source: FilesSource | None = None

        # Advanced autonomy loop (LLM-evaluated signals, scheduler, memory patterns)
        self.autonomy_loop = AutonomyLoop(
            config=config,
            memory=memory,
            scheduler=self.scheduler,
            tools=tools,
            client=self.engine.client,
        )

        # ── New systems ────────────────────────────────────────────────────
        # System 1: World Model
        self.world_model = WorldModel(config.data_dir / "world.db")

        # System 5: Production RAG (uses same DB as memory store)
        self.rag = RAGRetriever(config.db_path)
        self.rag_ingestion = IngestionPipeline(config.db_path)

        # System 2: Proactive Intelligence
        self.proactive = ProactiveEngine(
            data_dir=config.data_dir,
            inject_callback=self._on_proactive_signal,
        )

        # System 4: Integration Layer
        self.integrations = IntegrationManager.build_default(
            data_dir=config.data_dir,
            event_callback=self._on_integration_event,
            enable_gmail=False,    # requires OAuth credentials — user opts in
            enable_imessage=True,
            enable_health=False,   # requires export — user opts in
            enable_calendar=True,
        )

        # System 3: Long-Horizon Planning
        self.planning = PlanningEngine(config.data_dir)

        # System 6: Self-Improvement Loop
        self.improvement = SelfImprovementEngine(config.data_dir)

        # System 8: Multi-Agent Coordination
        self.agents = AgentCoordinator()

        # State
        self._tasks: dict[str, asyncio.Task] = {}
        self._notify_fn = None
        self._confirm_fn = None
        self._last_snapshot_at: float = 0.0

    # ---------- callbacks ----------

    def set_notify(self, fn) -> None:
        self._notify_fn = fn
        # Wire into AutonomyLoop too
        self.autonomy_loop.set_notify_callback(
            lambda msg, priority: fn(
                msg,
                str(priority.value) if hasattr(priority, "value") else str(priority),
            )
        )

    def set_confirm(self, fn) -> None:
        self._confirm_fn = fn
        self.router.gate._confirm = fn
        self.autonomy_loop.set_approval_callback(
            lambda desc: fn(Action(kind="autonomy", params={"desc": desc}, rationale=desc))
        )

    def _default_confirm(self, action: Action) -> bool:
        return False

    # ---------- lifecycle ----------

    async def start(self) -> None:
        logger.info("orchestrator starting")
        self.perception.subscribe(self._on_world_change)
        await self.engine.llm_queue.start()           # start serial LLM queue
        self._tasks["perception"] = asyncio.create_task(self.perception.run())
        self._tasks["autonomy_simple"] = asyncio.create_task(self._autonomy_loop())
        self._tasks["autonomy_advanced"] = asyncio.create_task(self.autonomy_loop.start())
        self._tasks["snapshots"] = asyncio.create_task(self._snapshot_loop())

        # New systems
        await self.proactive.start()
        await self.integrations.start()
        await self.agents.start()
        logger.info("all systems online")

    async def stop(self) -> None:
        logger.info("orchestrator stopping")
        self.perception.stop()
        self.autonomy_loop.stop()
        self.engine.llm_queue.stop()
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        await self.router.shutdown()
        if self._files_source:
            self._files_source.stop()
        self.scheduler.close()
        self.threads.close()

        # New systems
        await self.proactive.stop()
        await self.integrations.stop()
        await self.agents.stop()
        self.planning.close()
        self.improvement.close()

    async def stream(self, user_input: str, session_id: str):
        self.autonomy_loop.context_gate.update_activity()
        world_summary = self.perception.current().to_summary()
        async for ev in self.engine.stream(user_input, session_id, world_summary):
            yield ev

    # ---------- simple real-time autonomy (Mac system signals) ----------

    async def _autonomy_loop(self) -> None:
        await asyncio.sleep(5)  # let perception warm up
        while True:
            try:
                await self._autonomy_tick()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("autonomy tick: %s", e)
            await asyncio.sleep(AUTONOMY_POLL_S)

    async def _autonomy_tick(self) -> None:
        if self.config.default_mode == "passive":
            return
        all_signals: list[Signal] = []
        for src in self._sources:
            try:
                sigs = await src.poll()
                all_signals.extend(sigs)
            except Exception as e:
                logger.debug("source %s error: %s", src.source, e)

        for signal in all_signals:
            suggestion = self.reasoner.score(signal)
            if suggestion is None:
                continue
            score = suggestion.confidence.score
            can, reason = self.budget.can_notify(score)
            if not can:
                logger.debug("budget blocked %s: %s", signal.kind, reason)
                continue

            self.budget.record()
            if self._notify_fn:
                try:
                    self._notify_fn(suggestion.title, suggestion.rationale)
                except Exception:
                    pass

            if self.config.default_mode == "autonomous" and suggestion.action_kind:
                await self._auto_execute(suggestion)

    async def _auto_execute(self, suggestion) -> None:
        action = Action(
            kind=suggestion.action_kind,
            params=suggestion.action_params,
            rationale=suggestion.rationale,
        )
        result = await self.router.execute(action)
        if result.ok:
            logger.info("auto-executed %s via %s", action.kind, result.backend)
        else:
            logger.warning("auto-execute %s failed: %s", action.kind, result.error)

    # ---------- process hook: record user messages for improvement loop ----------

    async def process(self, user_input: str, session_id: str) -> tuple[str, TaskState]:
        self.autonomy_loop.context_gate.update_activity()
        self.improvement.on_user_message(user_input, context="user_request")

        # ── Tier 0: CommandRouter (zero LLM) ──────────────────────────────
        route = self.command_router.route(user_input)
        if route:
            try:
                record = await self._tools.execute(route.tool, route.params)
                response = str(record.result) if record.result is not None else (record.error or "Done.")
                trace = TaskState(goal=user_input, session_id=session_id)
                trace.observations.append(Event(
                    type=EventType.TOOL_CALL,
                    content={"name": route.tool, "args": route.params},
                ))
                trace.observations.append(Event(
                    type=EventType.TOOL_RESULT,
                    content=response,
                    metadata={"name": route.tool},
                ))
                trace.final_result = response
                logger.debug("Tier-0 routed: %s → %s", user_input[:40], route.tool)
                return response, trace
            except Exception as e:
                logger.warning("Tier-0 tool exec failed (%s): %s — falling to LLM", route.tool, e)

        # ── Tier 1: LLMQueue (serial, cached, deduped) → Tier 2: Gemini ──
        world_summary = self.perception.current().to_summary()
        return await self.engine.process(user_input, session_id, world_summary)

    # ---------- new system callbacks ----------

    def _on_proactive_signal(self, text: str) -> None:
        """Called when proactive engine surfaces a signal."""
        if self._notify_fn:
            try:
                self._notify_fn("ATLAS Proactive", text)
            except Exception:
                pass
        else:
            logger.info("Proactive signal: %s", text[:80])

    def _on_integration_event(self, events: list[dict]) -> None:
        """Route integration events to world model and proactive engine."""
        from atlas.world.models import WorldEvent
        for event in events:
            event_type = event.get("type", "")
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    we = WorldEvent(
                        event_type=event_type,
                        source=event.get("source", "integration"),
                        payload=event,
                    )
                    loop.create_task(self.world_model.record_event(we))
            except Exception:
                pass

            # Surface urgent emails as proactive signals
            if event_type == "email_received" and event.get("is_urgent"):
                sig = ProactiveSignal.create(
                    type=SignalType.EMAIL_URGENT,
                    source="gmail",
                    payload={"subject": event.get("subject", ""), "from": event.get("from", "")},
                    urgency=0.9,
                )
                self.proactive._batcher.enqueue(sig)

            # Surface calendar events
            if event_type == "calendar_event":
                etype = event.get("event_type", "")
                sig_type = SignalType.MEETING_NOW if etype == "meeting_now" else SignalType.CALENDAR_REMINDER
                sig = ProactiveSignal.create(
                    type=sig_type,
                    source="calendar",
                    payload={"title": event.get("title", "")},
                    urgency=0.95 if etype == "meeting_now" else 0.7,
                )
                self.proactive._batcher.enqueue(sig)

    # ---------- health aggregation ----------

    def system_health(self) -> dict:
        """Aggregate health from all 8 systems."""
        return {
            "trust":        self.engine.trust.health_check(),
            "world_model":  self.world_model.health_check(),
            "rag":          {"status": "healthy"},  # RAGRetriever has no persistent state to check
            "proactive":    self.proactive.health_check(),
            "integrations": self.integrations.health_check(),
            "planning":     self.planning.health_check(),
            "improvement":  self.improvement.health_check(),
            "agents":       self.agents.health_check(),
        }

    # ---------- perception callbacks ----------

    def _on_world_change(self, world) -> None:
        pass

    # ---------- snapshots ----------

    async def _snapshot_loop(self) -> None:
        while True:
            try:
                now = time.time()
                ws = self.perception.current()
                if not ws.is_idle() and now - self._last_snapshot_at > SNAPSHOT_INTERVAL_S:
                    self._last_snapshot_at = now
                    self.memory.snapshots.save(ws)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug("snapshot: %s", e)
            await asyncio.sleep(SNAPSHOT_INTERVAL_S)
