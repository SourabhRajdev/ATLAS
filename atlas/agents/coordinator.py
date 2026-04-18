"""AgentCoordinator — lifecycle manager for the multi-agent system."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from atlas.agents.base import BaseAgent
from atlas.agents.bus import MessageBus
from atlas.agents.models import AgentMessage, AgentRole, AgentTask, MessageKind
from atlas.agents.roles import (
    AnalystAgent, CommunicatorAgent, ExecutorAgent,
    GuardianAgent, OrchestratorAgent, ResearcherAgent,
)

logger = logging.getLogger("atlas.agents.coordinator")


class AgentCoordinator:
    """Manages all agents: starts/stops them and routes top-level requests."""

    def __init__(self, search_fn: Callable[[str], str] | None = None) -> None:
        self._bus = MessageBus()
        self._agents: dict[str, BaseAgent] = {}
        self._running = False
        self._search_fn = search_fn
        self._build_agents()

    def _build_agents(self) -> None:
        self._agents = {
            AgentRole.ORCHESTRATOR.value: OrchestratorAgent(self._bus),
            AgentRole.RESEARCHER.value: ResearcherAgent(self._bus, self._search_fn),
            AgentRole.EXECUTOR.value: ExecutorAgent(self._bus),
            AgentRole.COMMUNICATOR.value: CommunicatorAgent(self._bus),
            AgentRole.ANALYST.value: AnalystAgent(self._bus),
            AgentRole.GUARDIAN.value: GuardianAgent(self._bus),
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for agent in self._agents.values():
            await agent.start()
        logger.info("AgentCoordinator started with %d agents", len(self._agents))

    async def stop(self) -> None:
        self._running = False
        for agent in self._agents.values():
            await agent.stop()
        logger.info("AgentCoordinator stopped")

    async def submit(
        self,
        description: str,
        context: dict | None = None,
        intent: str = "",
        timeout: float = 30.0,
    ) -> dict:
        """Submit a top-level task to the orchestrator. Returns result dict."""
        if not self._running:
            raise RuntimeError("AgentCoordinator not started")

        orchestrator_inbox = self._bus.subscribe(AgentRole.ORCHESTRATOR.value)
        task = AgentTask.create(
            assigned_to=AgentRole.ORCHESTRATOR,
            description=description,
            context={"intent": intent, **(context or {})},
        )

        # Send directly to orchestrator's inbox
        assign_msg = AgentMessage.create(
            from_agent="coordinator",
            to_agent=AgentRole.ORCHESTRATOR,
            kind=MessageKind.TASK_ASSIGN,
            payload={
                "task_id": task.id,
                "description": task.description,
                "context": task.context,
            },
        )
        await self._bus.send(assign_msg)

        # Wait for result from orchestrator
        try:
            result_inbox = self._bus.subscribe("coordinator")
            while True:
                msg = await asyncio.wait_for(result_inbox.get(), timeout=timeout)
                if msg.kind == MessageKind.TASK_RESULT:
                    return msg.payload
        except asyncio.TimeoutError:
            return {"status": "timeout", "task_id": task.id}
        finally:
            self._bus.unsubscribe("coordinator")

    def get_agent(self, role: AgentRole) -> BaseAgent | None:
        return self._agents.get(role.value)

    def health_check(self) -> dict:
        agent_statuses = {
            role: agent.status() for role, agent in self._agents.items()
        }
        all_running = all(s["running"] for s in agent_statuses.values())
        return {
            "status": "healthy" if (self._running and all_running) else "down",
            "coordinator_running": self._running,
            "agents": agent_statuses,
            "bus_queue_depths": self._bus.queue_depths(),
            "message_history_size": len(self._bus.get_history()),
        }
