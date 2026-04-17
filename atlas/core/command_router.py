"""CommandRouter — Tier-0 fast path. Zero LLM calls.

Matches common voice/text commands via regex and routes them directly to
tool calls. This handles ~50% of real-world voice commands instantly and
for free — no Gemini call, no latency, no token cost.

Design principle from Google Assistant / Alexa:
  "If you can answer it with a rule, never use a model."

Add patterns here whenever you notice the LLM being called for something
that is always the same tool with the same mapping.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("atlas.command_router")


@dataclass
class Route:
    tool: str
    params: dict[str, Any]
    matched_by: str   # pattern label — for debugging / telemetry


# ---------------------------------------------------------------------------
# Pattern table
# Each entry: (label, compiled_regex, handler_fn -> Route | None)
# Patterns are tried in order — first match wins.
# ---------------------------------------------------------------------------

def _pct(s: str) -> int:
    """Parse '80%' or '80' → int 80."""
    return int(re.sub(r"[^0-9]", "", s) or "0")


_RAW: list[tuple[str, str, Any]] = [

    # ── Volume ──────────────────────────────────────────────────────────────
    ("volume_set",
     r"(?:set\s+)?(?:the\s+)?(?:volume|sound)\s+(?:to\s+)?(\d+)\s*%?",
     lambda m: Route("control_volume", {"level": int(m.group(1))}, "volume_set")),

    ("volume_max",
     r"(?:volume|sound)\s+(?:up|max|full|loud|louder|100)",
     lambda m: Route("control_volume", {"level": 100}, "volume_max")),

    ("volume_low",
     r"(?:volume|sound)\s+(?:down|low|lower|quiet|quieter|soft)",
     lambda m: Route("control_volume", {"level": 30}, "volume_low")),

    ("mute",
     r"^(?:mute|silence|shut up|quiet)[\s!.]*$",
     lambda m: Route("control_volume", {"level": 0}, "mute")),

    # ── Brightness ───────────────────────────────────────────────────────────
    ("brightness_set",
     r"(?:set\s+)?(?:the\s+)?(?:brightness|screen|display)\s+(?:to\s+)?(\d+)\s*%?",
     lambda m: Route("control_brightness", {"level": int(m.group(1))}, "brightness_set")),

    ("brightness_max",
     r"(?:brightness|screen|display)\s+(?:up|max|full|bright|brighter|100|all the way up)",
     lambda m: Route("control_brightness", {"level": 100}, "brightness_max")),

    ("brightness_low",
     r"(?:brightness|screen|display)\s+(?:down|low|lower|dim|dimmer|dark)",
     lambda m: Route("control_brightness", {"level": 20}, "brightness_low")),

    # ── Open app ─────────────────────────────────────────────────────────────
    ("open_app",
     r"^(?:open|launch|start|activate|switch to|go to|bring up)\s+(.+?)(?:\s+app(?:lication)?)?$",
     lambda m: Route("open_app", {"name": m.group(1).strip().title()}, "open_app")),

    # ── Open URL ─────────────────────────────────────────────────────────────
    ("open_url",
     r"(?:open|go to|navigate to|visit)\s+(https?://\S+)",
     lambda m: Route("open_url", {"url": m.group(1)}, "open_url")),

    ("open_url_bare",
     r"^(?:open|go to|navigate to|visit)\s+([\w.-]+\.(?:com|org|net|io|dev|ai|co|app|tv|me)\S*)$",
     lambda m: Route("open_url", {"url": "https://" + m.group(1)}, "open_url_bare")),

    # ── Time ─────────────────────────────────────────────────────────────────
    ("time",
     r"^(?:what(?:'s|\s+is)(?:\s+the)?\s+(?:time|current time)|what time is it|time|current time)[?.!]*$",
     lambda m: Route("get_current_time", {}, "time")),

    # ── Clipboard ────────────────────────────────────────────────────────────
    ("clipboard_get",
     r"^(?:what(?:'s|\s+is)(?:\s+(?:on|in))?\s+(?:my\s+)?clipboard|clipboard|show clipboard|read clipboard)[?.!]*$",
     lambda m: Route("get_clipboard", {}, "clipboard_get")),

    ("clipboard_set",
     r"^(?:copy|set clipboard(?:\s+to)?|clipboard set(?:\s+to)?)\s+(.+)$",
     lambda m: Route("set_clipboard", {"text": m.group(1).strip()}, "clipboard_set")),

    # ── Running apps ─────────────────────────────────────────────────────────
    ("running_apps",
     r"^(?:what(?:'s|\s+is)\s+(?:running|open)|list(?:\s+(?:all\s+)?(?:running\s+)?apps)?|running apps|open apps|what apps|what apps are running|what is running|what are running)[?.!]*$",
     lambda m: Route("list_running_apps", {}, "running_apps")),

    # ── Focused app ──────────────────────────────────────────────────────────
    ("frontmost",
     r"^(?:what(?:'s|\s+is)(?:\s+the)?\s+(?:focused|active|front(?:most)?|current)\s+app|what am i (?:using|on)|current app|active app)[?.!]*$",
     lambda m: Route("get_frontmost_app", {}, "frontmost")),

    # ── Spotlight search ─────────────────────────────────────────────────────
    ("spotlight",
     r"^(?:search|find|spotlight|look for|locate)\s+(.+)$",
     lambda m: Route("spotlight_search", {"query": m.group(1).strip()}, "spotlight")),

    # ── Speak ────────────────────────────────────────────────────────────────
    ("say",
     r"^(?:say|speak|tell me|read(?:\s+out)?)\s+(?!(?:the\s+)?screen\b)(.+)$",
     lambda m: Route("say_text", {"text": m.group(1).strip()}, "say")),

    # ── Show notification ─────────────────────────────────────────────────────
    ("notify",
     r"^(?:notify|notification|alert|show notification|remind me)\s+(?:me\s+)?(?:that\s+)?(.+)$",
     lambda m: Route("show_notification", {"title": "ATLAS", "message": m.group(1).strip()}, "notify")),

    # ── System info ──────────────────────────────────────────────────────────
    ("sysinfo",
     r"^(?:system info|sysinfo|machine info|hardware info|about this mac)[?.!]*$",
     lambda m: Route("get_system_info", {}, "sysinfo")),

    # ── Screen vision ────────────────────────────────────────────────────────────────────
    ("describe_screen",
     r"^(?:what(?:'s|\s+is|\s+do\s+you\s+see)(?:\s+on)?(?:\s+(?:my\s+)?screen)?|"
     r"look at(?:\s+(?:my\s+)?screen)?|describe(?:\s+(?:the\s+)?screen)?|"
     r"what\s+(?:am\s+i\s+(?:looking at|working on)|do\s+you\s+see)|"
     r"read\s+(?:the\s+)?screen|screen\s+(?:view|vision|check))[?.!]*$",
     lambda m: Route("describe_screen", {}, "describe_screen")),
]

# Compile all patterns once at import time
PATTERNS: list[tuple[str, re.Pattern, Any]] = [
    (label, re.compile(pattern, re.IGNORECASE), handler)
    for label, pattern, handler in _RAW
]


class CommandRouter:
    """Matches input against patterns and returns a Route (or None for LLM)."""

    def __init__(self) -> None:
        self._hits = 0
        self._misses = 0

    def route(self, user_input: str) -> Route | None:
        text = user_input.strip()
        for label, pattern, handler in PATTERNS:
            m = pattern.search(text)
            if m:
                try:
                    r = handler(m)
                    self._hits += 1
                    logger.debug("routed '%s' → %s (pattern: %s)", text[:40], r.tool, label)
                    return r
                except Exception as e:
                    logger.warning("route handler error (%s): %s", label, e)
                    continue
        self._misses += 1
        return None  # → falls through to LLM

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self._hits / total:.0%}" if total else "0%",
        }
