"""Engine — LLM facade with session history, context compression, and queue integration.

Call path:
  Orchestrator.process()
      → CommandRouter.route()         # Tier 0: zero-LLM fast path
      → LLMQueue.enqueue()            # Tier 1: serial queue + cache + dedup
      → Engine._process_llm()         # Tier 2: compressed context → Gemini
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from atlas.config import Settings
from atlas.core.executor import Executor
from atlas.core.llm_queue import LLMQueue, compress_history
from atlas.core.model_router import build_model_router
from atlas.core.models import Budget, EventType, Message, TaskState
from atlas.memory.store import MemoryStore
from atlas.tools.registry import ToolRegistry
from atlas.trust import TrustLayer

logger = logging.getLogger("atlas.engine")

# Verbatim turns kept in context — older turns are compressed to a summary.
# 3 turns = last 6 messages. Enough for follow-up corrections ("no, the other one").
MAX_VERBATIM_TURNS = 3


class Engine:
    def __init__(self, config: Settings, memory: MemoryStore, tools: ToolRegistry) -> None:
        self.config = config
        self.memory = memory
        self.tools = tools

        # ModelRouter: Gemini → Groq → Ollama failover
        self.model_router, self.client = build_model_router(config)

        # Trust layer — separate DB from main atlas.db
        self.trust = TrustLayer(db_path=config.data_dir / "trust.db")

        self.executor = Executor(
            model_router=self.model_router,
            config=config,
            tools=tools,
            memory=memory,
            trust=self.trust,
        )
        self._session_history: dict[str, list[dict]] = {}

        # LLMQueue wraps _process_llm so the orchestrator can use it directly
        self.llm_queue = LLMQueue(process_fn=self._process_llm)

    def set_approval_callback(self, fn: Callable) -> None:
        self.executor.approval_callback = fn

    def set_notify_callback(self, fn: Callable) -> None:
        self.executor.notify_callback = fn

    # ------------------------------------------------------------------ #
    #  Public entry — called by Orchestrator after routing               #
    # ------------------------------------------------------------------ #

    async def process(
        self,
        user_input: str,
        session_id: str,
        world_state_summary: str | None = None,
    ) -> tuple[str, TaskState]:
        """Enqueue query through LLMQueue (serial, cached, deduped)."""
        return await self.llm_queue.enqueue(user_input, session_id, world_state_summary)

    # ------------------------------------------------------------------ #
    #  Internal — actual LLM call (called by LLMQueue worker)            #
    # ------------------------------------------------------------------ #

    async def _process_llm(
        self,
        user_input: str,
        session_id: str,
        world_state_summary: str | None = None,
    ) -> tuple[str, TaskState]:
        self.memory.add_message(Message(
            session_id=session_id, role="user", content=user_input,
        ))

        trace = TaskState(goal=user_input, session_id=session_id)
        final_response = ""

        budget = Budget.for_query(user_input)
        budget.max_tokens = self.config.max_tokens
        budget.max_ms = 60_000
        budget.max_tool_calls = max(budget.max_tool_calls, 10)

        # Compress history: verbatim recent turns + compressed older turns
        raw_history = self._session_history.get(session_id, [])
        history = compress_history(raw_history)

        async for ev in self.executor.run(
            user_input, session_id, world_state_summary, budget, history,
        ):
            trace.observations.append(ev)
            if ev.type == EventType.DONE:
                final_response = ev.content if isinstance(ev.content, str) else str(ev.content)
            elif ev.type == EventType.ERROR and ev.metadata.get("fatal"):
                final_response = f"Error: {ev.content}"
                trace.success = False

        trace.final_result = final_response

        # Store only verbatim recent turns (never grow unbounded)
        raw_history.extend([
            {"role": "user",  "content": user_input},
            {"role": "model", "content": final_response},
        ])
        # Cap stored history at 20 turns (40 messages) to bound memory
        self._session_history[session_id] = raw_history[-40:]

        self.memory.add_message(Message(
            session_id=session_id, role="assistant", content=final_response,
        ))

        return final_response, trace

    async def stream(
        self,
        user_input: str,
        session_id: str,
        world_state_summary: str | None = None,
    ):
        raw_history = self._session_history.get(session_id, [])
        history = compress_history(raw_history)
        async for ev in self.executor.run(user_input, session_id, world_state_summary, history=history):
            yield ev
