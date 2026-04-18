"""Single-agent executor — streaming, stateful, resilient, parallel.

Call path:
  Engine._process_llm()
      → Executor.run()
          → ModelRouter.generate()   # Gemini → Groq → Ollama failover
          → ToolRegistry.execute()   # parallel tool calls
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Callable

from atlas.core.model_router import LLMResponse, ModelRouter, ToolCall
from atlas.core.models import Budget, Event, EventType, TaskState, Tier
from atlas.core.approval import describe_action, needs_confirmation
from atlas.tools.registry import ToolRegistry
from atlas.memory.store import MemoryStore
from atlas.trust import TrustLayer, TaintContext

logger = logging.getLogger("atlas.executor")

# Tools whose results contain external (untrusted) content.
# After any of these execute, _current_taint is upgraded to EXTERNAL
# so subsequent tool calls in the same loop inherit that taint level.
_EXTERNAL_CONTENT_TOOLS: frozenset[str] = frozenset({
    "web_search", "fetch_url",
    "gmail_get_messages", "gmail_get_thread", "gmail_search",
    "imessage_get_messages", "imessage_read",
    "github_get_issues", "github_get_prs",   # issue/PR bodies are untrusted
    "read_screen_text", "see_screen",         # screen content is untrusted
})


SYSTEM_PROMPT = """\
You are ATLAS — a Jarvis-level AI running natively on the user's MacBook Pro. \
You have full access to macOS and act decisively.

PERSONALITY:
- Confident, minimal, direct. Like Jarvis from Iron Man.
- Confirmations: 1–2 words max. "Done." "Opened." "Set." "Copied."
- Never say "I have", "I've", "I will", "I'll", "Sure", "Of course", "Certainly"
- Never repeat what the user said. Never add filler questions.
- If something fails, explain it in one sentence and offer the fix.

macOS CAPABILITIES (use them aggressively):
- open_app: open any app — Spotify, Safari, Terminal, Xcode, anything
- open_url: open URLs in browser
- show_notification: pop a macOS notification banner
- get_clipboard / set_clipboard: read and write clipboard
- control_volume: set or get system volume
- control_brightness: set screen brightness
- list_running_apps: see what's open
- type_text: type into the focused app
- press_keys: send keyboard shortcuts (cmd+c, cmd+shift+4, etc.)
- spotlight_search: find files/apps instantly via Spotlight
- say_text: speak aloud via macOS TTS
- run_shell: execute any allowlisted shell command (git, brew, npm, python3, osascript, etc.)
- read_file / write_file / list_directory / search_files: filesystem access
- web_search / fetch_url: internet research (Google via Serper)
- github_get_prs / github_get_issues / github_get_commits: GitHub awareness
- memory: remember preferences, facts, decisions across sessions

TOOL RULES:
- Use tools. Don't narrate what you're about to do — just do it.
- Don't call the same tool twice with the same args.
- open_app and open_url run without asking — just use them.
- write_file runs without asking — just write.
- Deletes always require explicit user confirmation — never delete autonomously.
- Chain tools efficiently: open + search + write in one go if the task requires it.
- If a tool fails with a semantic error, fix args and retry once.

CONTEXT AWARENESS:
You see the user's active app and window title in world state below.
Reference screen context naturally.

AUTONOMY:
You run a background loop watching calendar, mail, git, battery, and file changes.
Proactively surface important signals. In autonomous mode, act without asking.
"""


class Executor:
    def __init__(
        self,
        model_router: ModelRouter,
        config: Any,
        tools: ToolRegistry,
        memory: MemoryStore,
        trust: TrustLayer | None = None,
    ) -> None:
        self.model_router = model_router
        self.config = config
        self.tools = tools
        self.memory = memory
        self.trust = trust
        self.approval_callback: Callable | None = None
        self.notify_callback: Callable | None = None
        self.cancel_token: asyncio.Event | None = None
        # Taint context for current request — updated per execution
        self._current_taint: TaintContext = TaintContext.clean()

    # ------------------------------------------------------------------
    # Public entry point — async iterator of Events
    # ------------------------------------------------------------------

    async def run(
        self,
        goal: str,
        session_id: str,
        world_state_summary: str | None = None,
        budget: Budget | None = None,
        history: list[dict] | None = None,
    ) -> AsyncIterator[Event]:
        budget = budget or Budget.for_query(goal)
        state = TaskState(goal=goal, session_id=session_id)

        # Reset taint to CLEAN at the start of each request.
        # _execute_one() upgrades it if external-content tools are called.
        self._current_taint = TaintContext.clean()
        self._session_id = session_id

        state.messages = []
        if history:
            for h in history:
                state.messages.append({
                    "role": h["role"],
                    "parts": [{"text": h["content"]}],
                })

        intro = f"User: {goal}"
        if world_state_summary:
            intro = f"Current world state:\n{world_state_summary}\n\n{intro}"
        state.messages.append({"role": "user", "parts": [{"text": intro}]})

        seen_calls: set[str] = set()

        async for ev in self._agent_loop(state, budget, seen_calls):
            ev.task_id = state.id
            state.observations.append(ev)
            yield ev
            if ev.type == EventType.DONE:
                state.final_result = ev.content if isinstance(ev.content, str) else str(ev.content)
                return
            if ev.type == EventType.ERROR and ev.metadata.get("fatal"):
                state.success = False
                return
            if self.cancel_token and self.cancel_token.is_set():
                yield Event(type=EventType.ERROR, content="cancelled", metadata={"fatal": True})
                return

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------

    async def _agent_loop(
        self,
        state: TaskState,
        budget: Budget,
        seen_calls: set[str],
    ) -> AsyncIterator[Event]:
        tool_defs = self.tools.get_anthropic_tools()
        max_rounds = 10

        for _round in range(max_rounds):
            if budget.exhausted:
                yield Event(type=EventType.ERROR, content="budget exhausted",
                            metadata={"fatal": True})
                return

            # --- LLM call via ModelRouter (Gemini → Groq → Ollama) ---
            response: LLMResponse | None = None
            last_err = None
            for _attempt in range(3):
                try:
                    response = await self.model_router.generate(
                        state.messages, tool_defs, SYSTEM_PROMPT,
                    )
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    err_str = str(e)
                    if any(x in err_str.lower() for x in ("503", "unavailable", "502", "overloaded")):
                        logger.warning("LLM error (attempt %d): %s", _attempt + 1, e)
                        await asyncio.sleep(2 ** _attempt)
                        continue
                    break

            if last_err is not None:
                logger.error("LLM call failed: %s", last_err)
                yield Event(type=EventType.ERROR, content=f"llm: {last_err}",
                            metadata={"fatal": True})
                return

            if not response or (not response.text and not response.tool_calls):
                yield Event(type=EventType.DONE, content="(no response)")
                return

            # Append model turn to history
            model_parts: list[dict] = []
            if response.text:
                model_parts.append({"text": response.text})
            for tc in response.tool_calls:
                model_parts.append({"function_call": {"name": tc.name, "args": tc.args}})
            if model_parts:
                state.messages.append({"role": "model", "parts": model_parts})

            # Stream thought text
            if response.text:
                yield Event(type=EventType.THOUGHT, content=response.text)

            # No tool calls → done
            if not response.tool_calls:
                yield Event(type=EventType.DONE, content=response.text.strip() or "(no response)")
                return

            # Loop detection
            new_calls: list[ToolCall] = []
            for tc in response.tool_calls:
                sig = _call_signature(tc)
                if sig in seen_calls:
                    yield Event(type=EventType.ERROR,
                                content=f"loop detected: {tc.name}",
                                metadata={"recoverable": False})
                    yield Event(type=EventType.DONE,
                                content=response.text.strip() or "(stopped: loop detected)")
                    return
                seen_calls.add(sig)
                new_calls.append(tc)

            # Execute tool calls in parallel
            results = await self._execute_parallel(new_calls)
            budget.consume_tool_calls(len(new_calls))

            # Stream tool events + build function response parts
            fn_parts: list[dict] = []
            for tc, result in zip(new_calls, results):
                yield Event(
                    type=EventType.TOOL_CALL,
                    content={"name": tc.name, "args": tc.args},
                )
                yield Event(
                    type=EventType.TOOL_RESULT,
                    content=_compact(result),
                    metadata={"name": tc.name, "error": result.get("error")},
                )
                fn_parts.append({
                    "function_response": {
                        "name": tc.name,
                        "response": {"result": _compact(result)},
                    }
                })

            state.messages.append({"role": "user", "parts": fn_parts})

            # Checkpoint
            now = time.time()
            if now - state.last_checkpoint > 5:
                state.last_checkpoint = now

        yield Event(type=EventType.DONE, content="(max rounds reached)")

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_parallel(self, calls: list[ToolCall]) -> list[dict]:
        return await asyncio.gather(*[self._execute_one(tc) for tc in calls])

    async def _execute_one(self, tc: ToolCall) -> dict:
        name = tc.name
        params = tc.args or {}
        tool_def = self.tools.get_tool(name)

        if not tool_def:
            return {"error": f"unknown tool: {name}"}

        # --- Trust gate (runs before confirmation, before execution) ---
        if self.trust is not None:
            decision = await self.trust.gate(
                name, params, self._current_taint,
                session_id=getattr(self, "_session_id", ""),
            )
            if not decision.allowed:
                return {"error": f"trust: {decision.reason}"}
            # Trust layer can escalate AUTO/NOTIFY tools to require confirmation
            needs_trust_confirm = decision.requires_confirm
        else:
            needs_trust_confirm = False

        if needs_confirmation(tool_def, params) or needs_trust_confirm:
            desc = describe_action(tool_def, params)
            if needs_trust_confirm:
                desc = f"[external-source escalation] {desc}"
            approved = await self._request_approval(desc)
            if not approved:
                return {"error": "user denied"}

        # Snapshot before execution for rollback support
        snapshot_id: str | None = None
        if self.trust is not None:
            snapshot_id = await self.trust.snapshot_before(name, params)

        last_err = None
        for attempt in range(3):
            try:
                record = await self.tools.execute(name, params)
                if record.error:
                    if _is_transient_err(record.error):
                        last_err = record.error
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    return {"error": record.error}
                self.memory.log_action(record)
                if tool_def.tier == Tier.NOTIFY and self.notify_callback:
                    try:
                        nr = self.notify_callback(describe_action(tool_def, params))
                        if asyncio.iscoroutine(nr):
                            await nr
                    except Exception:
                        pass
                result_str = str(record.result) if record.result is not None else ""

                # Bug 1 fix: propagate taint from external-content tools.
                # If this tool returned external content, all subsequent tool
                # calls in this agent loop must treat that content as tainted.
                if name in _EXTERNAL_CONTENT_TOOLS and result_str:
                    self._current_taint = TaintContext.from_source(
                        "tool_result", content=result_str
                    )

                if self.trust is not None and snapshot_id:
                    # Record post-execution in audit (best effort)
                    try:
                        from atlas.trust.classifier import Decision as _D
                        await self.trust.record_result(
                            name, params, result_str,
                            decision if self.trust else _D(True, "no trust", 0),
                            session_id=getattr(self, "_session_id", ""),
                        )
                    except Exception:
                        pass
                return {"result": result_str, "_snapshot_id": snapshot_id or ""}
            except Exception as e:
                last_err = str(e)
                if attempt < 2 and _is_transient_err(last_err):
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                return {"error": f"{type(e).__name__}: {e}"}

        return {"error": f"retries exhausted: {last_err}"}

    async def _request_approval(self, description: str) -> bool:
        if self.approval_callback:
            result = self.approval_callback(description)
            if asyncio.iscoroutine(result):
                return await result
            return result
        logger.warning("No approval callback — denying: %s", description)
        return False


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _call_signature(tc: ToolCall) -> str:
    return f"{tc.name}:{json.dumps(tc.args, sort_keys=True, default=str)}"


def _compact(result: dict) -> str:
    if "error" in result:
        return f"ERROR: {result['error']}"
    s = result.get("result", "")
    if len(s) > 4000:
        return s[:4000] + f"\n... [truncated, {len(s) - 4000} more chars]"
    return s


def _is_transient_err(err: str) -> bool:
    s = err.lower()
    return any(k in s for k in (
        "timeout", "connection", "rate limit", "429",
        "503", "504", "temporarily", "reset",
    ))
