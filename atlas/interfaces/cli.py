"""CLI interface — Rich-powered REPL backed by the Orchestrator."""

from __future__ import annotations

import asyncio
import logging
import uuid

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.theme import Theme

from atlas.config import Settings
from atlas.control.models import Action
from atlas.core.orchestrator import Orchestrator
from atlas.memory.store import MemoryStore
from atlas.tools.registry import ToolRegistry

logger = logging.getLogger("atlas.cli")

theme = Theme({
    "info": "dim cyan",
    "warning": "magenta",
    "danger": "bold red",
    "user": "bold green",
    "atlas": "bold blue",
    "cost": "dim yellow",
    "trace": "dim white",
})
console = Console(theme=theme)


def _confirm_action(action: Action) -> bool:
    console.print(f"\n[warning]Action requires approval:[/warning]")
    console.print(f"  {action.kind}: {action.rationale or action.params}")
    return Confirm.ask("  Allow?", default=False)


def _notify(title: str, rationale: str) -> None:
    console.print(f"\n[info]ATLAS:[/info] {title}")
    if rationale:
        console.print(f"  [trace]{rationale}[/trace]")


async def run_cli(config: Settings, memory: MemoryStore, tools: ToolRegistry) -> None:
    orch = Orchestrator(config, memory, tools)
    orch.set_confirm(_confirm_action)
    orch.set_notify(_notify)
    async def approval_callback(desc: str) -> bool:
        return Confirm.ask(f"  Allow: {desc}?", default=False)

    orch.engine.set_approval_callback(approval_callback)

    session_id = uuid.uuid4().hex[:8]

    await orch.start()

    console.print(Panel.fit(
        "[atlas]ATLAS[/atlas] — Local Execution Intelligence\n"
        f"Session: {session_id} | Model: {config.model}\n"
        f"Tools: {len(tools.get_anthropic_tools())} | Mode: {config.default_mode}\n"
        f"Perception: {'active' if orch.perception.monitor.available else 'limited'}\n"
        "Type [bold]/help[/bold] for commands, [bold]/quit[/bold] to exit",
        border_style="blue",
    ))

    # Startup perception display
    await asyncio.sleep(0.5)
    world = orch.perception.current()
    if world.active_app:
        win = f" > {world.active_window_title}" if world.active_window_title else ""
        console.print(f"[dim]Watching: {world.active_app}{win}[/dim]\n")

    try:
        while True:
            try:
                user_input = Prompt.ask("\n[user]you[/user]")
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input.strip():
                continue

            # Voice triggers — natural language shortcuts
            if user_input.lower().strip() in {
                "voice activate", "voice mode", "start voice",
                "listen", "hey atlas", "voice on", "activate voice",
                "voice",
            }:
                user_input = "/voice"

            if user_input.startswith("/"):
                result = await _handle_command(user_input, orch, session_id, config, memory)
                if result == "quit":
                    break
                continue

            try:
                console.print()
                with console.status("[info]thinking...[/info]", spinner="dots"):
                    response, trace = await orch.process(user_input, session_id)

                console.print(Panel(
                    trace.to_display(),
                    title="[trace]Trace[/trace]",
                    border_style="dim",
                    padding=(0, 1),
                ))
                console.print()
                console.print(Markdown(response))

                cost = memory.get_session_cost(session_id)
                if cost > 0:
                    console.print(f"[cost]  session: ${cost:.4f}[/cost]")

            except Exception as e:
                console.print(f"[danger]Error: {type(e).__name__}: {e}[/danger]")
                logger.exception("processing error")
    finally:
        await orch.stop()
        cost = memory.get_session_cost(session_id)
        total = memory.get_total_cost()
        console.print(f"\n[info]Session: ${cost:.4f} | Total: ${total:.4f}[/info]")
        console.print("[atlas]ATLAS offline.[/atlas]\n")


async def _handle_command(
    cmd: str,
    orch: Orchestrator,
    session_id: str,
    config: Settings,
    memory: MemoryStore,
) -> str | None:
    parts = cmd.strip().split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command in ("/quit", "/exit", "/q"):
        return "quit"

    elif command == "/help":
        console.print(Panel(
            "[bold]/quit[/bold]           — exit\n"
            "[bold]/cost[/bold]           — session + all-time cost\n"
            "[bold]/memories[/bold]       — stored memories\n"
            "[bold]/remember <text>[/bold] — store a memory\n"
            "[bold]/search <query>[/bold]  — semantic memory search\n"
            "[bold]/research <topic>[/bold] — web research\n"
            "[bold]/log[/bold]            — recent actions\n"
            "[bold]/world[/bold]          — current perception state\n"
            "[bold]/history[/bold]        — recent world snapshots\n"
            "[bold]/mode [mode][/bold]    — show/set mode (passive/assistive/autonomous)\n"
            "[bold]/pause [secs][/bold]   — pause perception\n"
            "[bold]/capabilities[/bold]   — granted capabilities\n"
            "[bold]/undo[/bold]           — recent undo log\n"
            "[bold]/voice[/bold]          — push-to-talk voice input\n"
            "[bold]/voice continuous[/bold] — continuous voice mode\n"
            "[bold]/status[/bold]         — all 8 system health checks\n"
            "[bold]/clear[/bold]          — clear screen\n"
            "[bold]/new[/bold]            — new session",
            title="Commands",
            border_style="blue",
        ))

    elif command == "/cost":
        console.print(f"Session: ${memory.get_session_cost(session_id):.4f}")
        console.print(f"All-time: ${memory.get_total_cost():.4f}")
        q = orch.engine.llm_queue.stats()
        r = orch.command_router.stats()
        console.print(
            f"[dim]LLM calls: {q['llm_calls']} / {q['enqueued']} requests "
            f"({q['savings']} saved by cache+router) | "
            f"Router hit rate: {r['hit_rate']} | "
            f"Queue depth: {q['queue_depth']}[/dim]"
        )

    elif command == "/memories":
        mems = memory.get_recent_memories(limit=20)
        if not mems:
            console.print("[info]No memories stored yet.[/info]")
        else:
            for m in mems:
                console.print(f"  [{m['type']}] {m['content']}")

    elif command == "/remember":
        if arg:
            from atlas.core.models import MemoryEntry
            entry = MemoryEntry(type="fact", content=arg, source=f"session:{session_id}")
            memory.add_memory(entry)
            console.print(f"[info]Saved: {arg}[/info]")
        else:
            console.print("[warning]Usage: /remember <text>[/warning]")

    elif command == "/search":
        if not arg:
            console.print("[warning]Usage: /search <query>[/warning]")
        else:
            results = memory.semantic.search(arg, limit=5)
            if not results:
                # Fall back to FTS
                results = memory.search_memories(arg, limit=5)
                for r in results:
                    console.print(f"  [{r.get('type', '?')}] {r['content'][:80]}")
            else:
                for r in results:
                    console.print(f"  [{r['source']}] (score:{r['score']:.2f}) {r['text'][:80]}")

    elif command == "/research":
        if not arg:
            console.print("[warning]Usage: /research <topic>[/warning]")
        else:
            prompt = (
                f"Research the following topic thoroughly:\n\n{arg}\n\n"
                "1. Search the web for current information\n"
                "2. Fetch and read the 2-3 most relevant results\n"
                "3. Synthesize into a structured summary with sources\n"
                "4. Save as markdown in ~/atlas-research/"
            )
            try:
                console.print()
                with console.status("[info]researching...[/info]", spinner="dots"):
                    response, trace = await orch.process(prompt, session_id)
                console.print(Panel(trace.to_display(), title="[trace]Research[/trace]", border_style="dim"))
                console.print()
                console.print(Markdown(response))
            except Exception as e:
                console.print(f"[danger]Research failed: {e}[/danger]")

    elif command == "/log":
        rows = memory.get_recent_actions(limit=15)
        if not rows:
            console.print("[info]No actions logged.[/info]")
        else:
            for r in rows:
                status = "OK" if not r.get("error") else "ERR"
                console.print(f"  [{r['created_at'][-8:]}] {r['tool_name']} (tier:{r['tier']}) {status}")

    elif command == "/voice":
        try:
            from atlas.interfaces.voice import VoiceSession
            mode = arg.strip() if arg.strip() in ("once", "continuous") else "once"
            session = VoiceSession(orch, session_id, config)
            if mode == "continuous":
                await session.run_continuous()
            else:
                await session.run_once()
        except ImportError as e:
            console.print(f"[warning]Voice deps missing: {e}[/warning]")
            console.print("  pip install pyaudio faster-whisper elevenlabs numpy")
        except Exception as e:
            console.print(f"[danger]Voice error: {e}[/danger]")

    elif command == "/world":
        ws = orch.perception.current()
        console.print(Panel(ws.to_display(), title="World State", border_style="cyan"))

    elif command == "/history":
        snaps = memory.snapshots.latest(10)
        if not snaps:
            console.print("[info]No snapshots yet.[/info]")
        else:
            table = Table(title="Recent Snapshots")
            table.add_column("Time", style="dim")
            table.add_column("App", style="cyan")
            table.add_column("Window", style="white")
            for s in reversed(snaps):
                import datetime
                ts = datetime.datetime.fromtimestamp(s["ts"]).strftime("%H:%M:%S")
                table.add_row(ts, s.get("app", ""), (s.get("window", "") or "")[:40])
            console.print(table)

    elif command == "/mode":
        if not arg:
            console.print(f"[info]Mode: {config.default_mode}[/info]")
            console.print("  passive — no proactive actions")
            console.print("  assistive — notify only (default)")
            console.print("  autonomous — act + notify")
        elif arg in ("passive", "assistive", "autonomous"):
            config.default_mode = arg
            console.print(f"[info]Mode → {arg}[/info]")
        else:
            console.print("[warning]Invalid mode[/warning]")

    elif command == "/pause":
        secs = int(arg) if arg.isdigit() else 300
        orch.perception.pause(secs)
        console.print(f"[info]Perception paused for {secs}s[/info]")

    elif command == "/capabilities":
        caps = orch.router.gate.granted()
        for c in sorted(caps, key=lambda x: x.value):
            console.print(f"  [info]{c.value}[/info]")

    elif command == "/undo":
        tokens = orch.router.undo.recent(10)
        if not tokens:
            console.print("[info]No undo history.[/info]")
        else:
            for t in reversed(tokens):
                import datetime
                ts = datetime.datetime.fromtimestamp(t.created_at).strftime("%H:%M")
                console.print(f"  [{ts}] {t.kind} via {t.backend} (id:{t.id})")

    elif command == "/status":
        try:
            health = orch.system_health()
        except Exception as e:
            console.print(f"[danger]health_check failed: {e}[/danger]")
            return None

        table = Table(title="ATLAS System Health", border_style="blue")
        table.add_column("System", style="bold cyan", width=18)
        table.add_column("Status", width=10)
        table.add_column("Details", style="dim")

        STATUS_STYLE = {
            "healthy":  "[bold green]healthy[/bold green]",
            "degraded": "[bold yellow]degraded[/bold yellow]",
            "down":     "[bold red]down[/bold red]",
        }

        def _fmt(h: dict, label: str) -> None:
            st = h.get("status", "unknown")
            styled = STATUS_STYLE.get(st, f"[white]{st}[/white]")
            details_parts = []
            for k, v in h.items():
                if k == "status":
                    continue
                if isinstance(v, dict):
                    continue
                details_parts.append(f"{k}={v}")
            table.add_row(label, styled, "  ".join(details_parts[:3]))

        _fmt(health["trust"],       "Trust Layer")
        _fmt(health["world_model"], "World Model")
        _fmt(health["rag"],         "RAG (Production)")
        _fmt(health["proactive"],   "Proactive Engine")

        # Integrations — expand per-integration
        int_health = health["integrations"]
        int_status = int_health.get("status", "down")
        int_styled = STATUS_STYLE.get(int_status, int_status)
        int_details = "  ".join(
            f"{name}={ih.get('status','?')}"
            for name, ih in int_health.get("integrations", {}).items()
        )
        table.add_row("Integrations", int_styled, int_details)

        _fmt(health["planning"],    "Planning")
        _fmt(health["improvement"], "Self-Improvement")

        ag_health = health["agents"]
        ag_status = ag_health.get("status", "down")
        ag_styled = STATUS_STYLE.get(ag_status, ag_status)
        ag_details = f"agents={len(ag_health.get('agents', {}))}"
        table.add_row("Multi-Agent", ag_styled, ag_details)

        console.print(table)

    elif command == "/clear":
        console.clear()

    elif command == "/new":
        console.print(f"[info]New session. Previous: {session_id}[/info]")

    else:
        console.print(f"[warning]Unknown: {command}. /help[/warning]")

    return None
