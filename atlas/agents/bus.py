"""Message bus — in-process async pub/sub for agent communication.

Each agent subscribes to its own queue. The bus delivers messages to the
appropriate queue. Broadcast messages go to all subscribers.

Thread-safe: uses asyncio.Queue per agent. No network, no serialization.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from atlas.agents.models import AgentMessage, AgentRole

logger = logging.getLogger("atlas.agents.bus")


class MessageBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._subscribers: set[str] = set()
        self._history: list[AgentMessage] = []
        self._max_history = 500

    def subscribe(self, agent_id: str) -> asyncio.Queue[AgentMessage]:
        """Register agent and return its inbox queue."""
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()
            self._subscribers.add(agent_id)
            logger.debug("Agent subscribed: %s", agent_id)
        return self._queues[agent_id]

    def unsubscribe(self, agent_id: str) -> None:
        self._queues.pop(agent_id, None)
        self._subscribers.discard(agent_id)

    async def send(self, message: AgentMessage) -> None:
        """Deliver message to target agent(s)."""
        self._record(message)

        if message.to_agent == "broadcast":
            for agent_id, queue in self._queues.items():
                if agent_id != message.from_agent:
                    await queue.put(message)
        elif message.to_agent in self._queues:
            await self._queues[message.to_agent].put(message)
        else:
            logger.warning("No subscriber for agent: %s", message.to_agent)

    def send_nowait(self, message: AgentMessage) -> None:
        """Non-blocking send. Drops if queue is full."""
        self._record(message)

        if message.to_agent == "broadcast":
            for agent_id, queue in self._queues.items():
                if agent_id != message.from_agent:
                    try:
                        queue.put_nowait(message)
                    except asyncio.QueueFull:
                        logger.warning("Queue full for %s — message dropped", agent_id)
        elif message.to_agent in self._queues:
            try:
                self._queues[message.to_agent].put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("Queue full for %s — message dropped", message.to_agent)

    def _record(self, message: AgentMessage) -> None:
        self._history.append(message)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def get_history(self, limit: int = 50) -> list[AgentMessage]:
        return list(self._history[-limit:])

    def queue_depths(self) -> dict[str, int]:
        return {agent_id: q.qsize() for agent_id, q in self._queues.items()}
