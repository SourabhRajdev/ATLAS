"""BaseAgent — abstract agent with message loop and task handling."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from atlas.agents.bus import MessageBus
from atlas.agents.models import AgentMessage, AgentRole, AgentTask, MessageKind, TaskStatus

logger = logging.getLogger("atlas.agents")


class BaseAgent(ABC):
    role: AgentRole

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._inbox = bus.subscribe(self.role.value)
        self._running = False
        self._task: asyncio.Task | None = None
        self._current_tasks: dict[str, AgentTask] = {}
        self._log = logging.getLogger(f"atlas.agents.{self.role.value}")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=f"agent_{self.role.value}")
        self._log.info("Agent started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._log.info("Agent stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                msg = await asyncio.wait_for(self._inbox.get(), timeout=1.0)
                await self._handle_message(msg)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._log.error("Message handling error: %s", e)

    async def _handle_message(self, msg: AgentMessage) -> None:
        if msg.kind == MessageKind.TASK_ASSIGN:
            task = AgentTask.create(
                assigned_to=self.role,
                description=msg.payload.get("description", ""),
                context=msg.payload.get("context", {}),
                parent_task_id=msg.payload.get("parent_task_id"),
            )
            task.id = msg.payload.get("task_id", task.id)
            self._current_tasks[task.id] = task
            asyncio.create_task(self._run_task(task, msg))
        elif msg.kind == MessageKind.STATUS_REQ:
            resp = AgentMessage.create(
                from_agent=self.role,
                to_agent=msg.from_agent,
                kind=MessageKind.STATUS_RESP,
                payload=self.status(),
                reply_to=msg.id,
            )
            await self._bus.send(resp)
        elif msg.kind == MessageKind.VETO:
            await self._on_veto(msg)
        else:
            await self._on_message(msg)

    async def _run_task(self, task: AgentTask, original_msg: AgentMessage) -> None:
        task.start()
        try:
            result = await self.handle_task(task)
            task.complete(result)
            reply = AgentMessage.create(
                from_agent=self.role,
                to_agent=original_msg.from_agent,
                kind=MessageKind.TASK_RESULT,
                payload={"task_id": task.id, "status": "done", "result": result},
                reply_to=original_msg.id,
            )
        except Exception as e:
            task.fail(str(e))
            self._log.error("Task %s failed: %s", task.id, e)
            reply = AgentMessage.create(
                from_agent=self.role,
                to_agent=original_msg.from_agent,
                kind=MessageKind.TASK_RESULT,
                payload={"task_id": task.id, "status": "failed", "error": str(e)},
                reply_to=original_msg.id,
            )
        await self._bus.send(reply)

    @abstractmethod
    async def handle_task(self, task: AgentTask) -> Any:
        """Process an assigned task and return result."""

    async def _on_message(self, msg: AgentMessage) -> None:
        """Handle non-task messages. Override to add behavior."""

    async def _on_veto(self, msg: AgentMessage) -> None:
        task_id = msg.payload.get("task_id")
        if task_id and task_id in self._current_tasks:
            self._current_tasks[task_id].status = TaskStatus.CANCELLED
            self._log.warning("Task %s vetoed by %s", task_id, msg.from_agent)

    def status(self) -> dict:
        return {
            "role": self.role.value,
            "running": self._running,
            "active_tasks": len([t for t in self._current_tasks.values()
                                  if t.status == TaskStatus.RUNNING]),
            "total_tasks": len(self._current_tasks),
        }

    async def send(
        self,
        to: AgentRole | str,
        kind: MessageKind,
        payload: dict,
        reply_to: str | None = None,
    ) -> None:
        msg = AgentMessage.create(
            from_agent=self.role,
            to_agent=to,
            kind=kind,
            payload=payload,
            reply_to=reply_to,
        )
        await self._bus.send(msg)
