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
from atlas.memory.store import MemoryStore
from atlas.perception.daemon import PerceptionDaemon
from atlas.scheduler.scheduler import Scheduler
from atlas.tools.registry import ToolRegistry

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

    # ---------- user request ----------

    async def process(self, user_input: str, session_id: str) -> tuple[str, TaskState]:
        self.autonomy_loop.context_gate.update_activity()

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
                # Fall through to LLM on failure

        # ── Tier 1: LLMQueue (serial, cached, deduped) → Tier 2: Gemini ──
        world_summary = self.perception.current().to_summary()
        return await self.engine.process(user_input, session_id, world_summary)

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
