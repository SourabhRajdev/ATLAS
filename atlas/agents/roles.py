"""Concrete agent implementations for each role."""

from __future__ import annotations

import logging
from typing import Any, Callable

from atlas.agents.base import BaseAgent
from atlas.agents.bus import MessageBus
from atlas.agents.models import AgentMessage, AgentRole, AgentTask, MessageKind

logger = logging.getLogger("atlas.agents.roles")


class OrchestratorAgent(BaseAgent):
    """Decomposes complex requests into sub-tasks and routes them to specialist agents."""
    role = AgentRole.ORCHESTRATOR

    def __init__(self, bus: MessageBus) -> None:
        super().__init__(bus)
        self._pending_results: dict[str, list] = {}  # parent_task_id → results

    async def handle_task(self, task: AgentTask) -> Any:
        intent = task.context.get("intent", "")
        query = task.description

        # Route based on intent
        sub_tasks = self._plan_sub_tasks(query, intent, task.id)

        if not sub_tasks:
            return {"answer": f"Completed: {query}", "sub_tasks": 0}

        # Assign sub-tasks (fire and forget here — results arrive via TASK_RESULT)
        for role, description, ctx in sub_tasks:
            sub = AgentTask.create(
                assigned_to=role,
                description=description,
                context={**ctx, "parent_task_id": task.id},
                parent_task_id=task.id,
            )
            await self.send(role, MessageKind.TASK_ASSIGN, {
                "task_id": sub.id,
                "description": sub.description,
                "context": sub.context,
                "parent_task_id": task.id,
            })

        return {"routed": True, "sub_tasks": len(sub_tasks)}

    def _plan_sub_tasks(
        self, query: str, intent: str, parent_id: str
    ) -> list[tuple[AgentRole, str, dict]]:
        q = query.lower()
        tasks = []

        if "research" in q or "find" in q or "what is" in q or intent == "research":
            tasks.append((AgentRole.RESEARCHER, f"Research: {query}", {"query": query}))

        if "send" in q or "email" in q or "message" in q or intent == "communicate":
            tasks.append((AgentRole.COMMUNICATOR, f"Draft: {query}", {"content": query}))

        if "run" in q or "execute" in q or "create file" in q or intent == "execute":
            tasks.append((AgentRole.EXECUTOR, f"Execute: {query}", {"command": query}))

        if "analyze" in q or "summarize" in q or "trend" in q or intent == "analyze":
            tasks.append((AgentRole.ANALYST, f"Analyze: {query}", {"data": query}))

        if "safe" in q or "risk" in q or "dangerous" in q or intent == "review":
            tasks.append((AgentRole.GUARDIAN, f"Review: {query}", {"action": query}))

        return tasks

    async def _on_message(self, msg: AgentMessage) -> None:
        if msg.kind == MessageKind.TASK_RESULT:
            parent_id = msg.payload.get("parent_task_id") or ""
            if parent_id:
                self._pending_results.setdefault(parent_id, []).append(msg.payload)
        elif msg.kind == MessageKind.ESCALATE:
            self._log.warning("Escalation from %s: %s", msg.from_agent, msg.payload)


class ResearcherAgent(BaseAgent):
    """Searches memory, RAG, and web for information."""
    role = AgentRole.RESEARCHER

    def __init__(self, bus: MessageBus, search_fn: Callable[[str], str] | None = None) -> None:
        super().__init__(bus)
        self._search = search_fn

    async def handle_task(self, task: AgentTask) -> Any:
        query = task.context.get("query", task.description)
        if self._search:
            result = self._search(query)
            return {"query": query, "result": result, "source": "search"}
        return {"query": query, "result": f"[Research result for: {query}]", "source": "mock"}


class ExecutorAgent(BaseAgent):
    """Executes approved actions — file ops, shell, tool calls."""
    role = AgentRole.EXECUTOR

    async def handle_task(self, task: AgentTask) -> Any:
        command = task.context.get("command", task.description)
        # Execution is handed off to the main executor — just signal intent here
        return {"command": command, "status": "queued_for_execution"}


class CommunicatorAgent(BaseAgent):
    """Drafts and sends communications — email, messages, notifications."""
    role = AgentRole.COMMUNICATOR

    async def handle_task(self, task: AgentTask) -> Any:
        content = task.context.get("content", task.description)
        return {"draft": f"Draft communication: {content}", "status": "draft_ready"}


class AnalystAgent(BaseAgent):
    """Analyzes data, produces summaries, identifies trends."""
    role = AgentRole.ANALYST

    async def handle_task(self, task: AgentTask) -> Any:
        data = task.context.get("data", task.description)
        return {"analysis": f"Analysis of: {data[:100]}", "insights": []}


class GuardianAgent(BaseAgent):
    """Reviews actions for safety, privacy, and policy compliance.

    The Guardian is the only agent that can veto actions from other agents.
    It checks against the trust layer before approving execution.
    """
    role = AgentRole.GUARDIAN

    _HIGH_RISK_KEYWORDS = frozenset({
        "delete", "rm", "drop", "format", "wipe", "send email", "post",
        "purchase", "pay", "transfer", "publish",
    })

    async def handle_task(self, task: AgentTask) -> Any:
        action = task.context.get("action", task.description).lower()
        risk_words = [w for w in self._HIGH_RISK_KEYWORDS if w in action]

        if risk_words:
            risk = "high"
            recommendation = f"Requires explicit user approval — risky keywords: {risk_words}"
            verdict = "needs_approval"
        else:
            risk = "low"
            recommendation = "Action appears safe to proceed."
            verdict = "approved"

        return {
            "action": action[:200],
            "risk": risk,
            "verdict": verdict,
            "recommendation": recommendation,
        }

    async def _on_message(self, msg: AgentMessage) -> None:
        """Guardian can also receive broadcast requests to review actions."""
        if msg.kind == MessageKind.BROADCAST:
            action = msg.payload.get("action", "")
            action_lower = action.lower()
            risk_words = [w for w in self._HIGH_RISK_KEYWORDS if w in action_lower]
            if risk_words:
                veto = AgentMessage.create(
                    from_agent=self.role,
                    to_agent=msg.from_agent,
                    kind=MessageKind.VETO,
                    payload={
                        "task_id": msg.payload.get("task_id", ""),
                        "reason": f"High-risk action detected: {risk_words}",
                    },
                    reply_to=msg.id,
                )
                await self._bus.send(veto)
