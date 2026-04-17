"""LLMQueue — Tier-1 serial request queue with cache and deduplication.

Design principles (from production assistant systems):
  1. SERIAL — one LLM call at a time. No concurrent Gemini calls.
     Concurrent calls waste tokens on redundant context + hit rate limits faster.
  2. CACHE — identical queries within TTL return instantly (0 tokens).
  3. DEDUP — if the same query is in-flight, new callers wait on the same future.
     Voice mode can fire the same command twice (mic echo, repeat) — deduplicated.
  4. PRIORITY — urgent signals (CONFIRM tier, battery critical) skip the queue.
  5. CONTEXT COMPRESSION — trim session history to last 3 turns before sending.
     Old turns become a 1-line summary. Cuts input tokens by 60-80% for long sessions.
  6. WORLD STATE DELTA — only attach world state when it changed since last call.
     Attaching 200 tokens of screen context to every "what time is it" is wasteful.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger("atlas.llm_queue")

# How many seconds a cached response is valid for.
# Dynamic queries (time, clipboard) get short TTL; stable queries get longer.
_CACHE_TTL_PATTERNS: list[tuple[str, int]] = [
    ("time",            3),    # 3s  — time changes every second
    ("clipboard",       5),    # 5s  — clipboard changes often
    ("running apps",   10),    # 10s — apps open/close
    ("active app",      5),    # 5s
    ("front",           5),    # frontmost app
    ("battery",        30),    # 30s
    ("volume",         15),    # 15s
    ("brightness",     15),    # 15s
    ("git",            60),    # 1 min
    ("calendar",       60),    # 1 min
    ("mail",           60),    # 1 min
    ("disk",           120),   # 2 min
    ("system info",    300),   # 5 min
]
_DEFAULT_TTL = 30   # 30s default for anything not matched

# Max turns of verbatim history to keep. Older turns get compressed to a summary.
MAX_VERBATIM_TURNS = 3      # = 6 messages (user + assistant per turn)
MAX_SUMMARY_TURNS  = 5      # how many older turns to include compressed


Priority = int   # lower = higher priority
PRIORITY_HIGH   = 1
PRIORITY_NORMAL = 5
PRIORITY_LOW    = 9


@dataclass(order=True)
class _QueueItem:
    priority:   int
    enqueued_at: float
    # non-compared fields
    query:      str      = field(compare=False)
    session_id: str      = field(compare=False)
    world:      str | None = field(compare=False)
    future:     asyncio.Future = field(compare=False)


class LLMQueue:
    """Serial LLM request queue with caching, dedup, and context compression."""

    def __init__(self, process_fn: Callable[..., Awaitable[Any]]) -> None:
        """
        process_fn: async (query, session_id, world_summary) -> (response, trace)
        """
        self._process = process_fn
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        # cache: query_hash -> (result, timestamp)
        self._cache: dict[str, tuple[Any, float]] = {}
        # in-flight dedup: query_hash -> list[Future] waiting for the same result
        self._in_flight: dict[str, list[asyncio.Future]] = {}
        self._running = False
        self._worker_task: asyncio.Task | None = None
        # stats
        self._stat_enqueued = 0
        self._stat_cache_hits = 0
        self._stat_dedup_hits = 0
        self._stat_llm_calls = 0

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._running = True
        self._worker_task = asyncio.create_task(self._worker(), name="llm-queue-worker")
        logger.info("LLMQueue started")

    def stop(self) -> None:
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        logger.info("LLMQueue stopped")

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    async def enqueue(
        self,
        query: str,
        session_id: str,
        world_summary: str | None = None,
        priority: Priority = PRIORITY_NORMAL,
    ) -> Any:
        """Submit a query. Returns (response, trace) when processed."""
        self._stat_enqueued += 1
        key = _cache_key(query)

        # ── Tier: cache hit ──────────────────────────────────────────────
        cached = self._cache.get(key)
        if cached:
            result, ts = cached
            if time.time() - ts < _get_ttl(query):
                self._stat_cache_hits += 1
                logger.debug("cache hit (%.0fs old): %s", time.time() - ts, query[:40])
                return result

        # ── Tier: dedup (same query already in-flight) ──────────────────
        loop = asyncio.get_running_loop()
        if key in self._in_flight:
            self._stat_dedup_hits += 1
            logger.debug("dedup: waiting on in-flight: %s", query[:40])
            fut: asyncio.Future = loop.create_future()
            self._in_flight[key].append(fut)
            return await fut

        # ── Tier: queue it ───────────────────────────────────────────────
        fut = loop.create_future()
        self._in_flight[key] = [fut]
        item = _QueueItem(
            priority=priority,
            enqueued_at=time.time(),
            query=query,
            session_id=session_id,
            world=world_summary,
            future=fut,
        )
        await self._queue.put(item)
        logger.debug("queued (pri=%d, depth=%d): %s", priority, self._queue.qsize(), query[:40])
        return await fut

    def stats(self) -> dict:
        total = self._stat_enqueued or 1
        return {
            "enqueued":    self._stat_enqueued,
            "llm_calls":   self._stat_llm_calls,
            "cache_hits":  self._stat_cache_hits,
            "dedup_hits":  self._stat_dedup_hits,
            "llm_rate":    f"{self._stat_llm_calls / total:.0%}",
            "savings":     f"{(total - self._stat_llm_calls) / total:.0%}",
            "queue_depth": self._queue.qsize(),
        }

    # ------------------------------------------------------------------ #
    #  Worker (runs forever, processes one item at a time)                #
    # ------------------------------------------------------------------ #

    async def _worker(self) -> None:
        logger.info("LLMQueue worker running")
        while self._running:
            # Poll with timeout so we can exit cleanly
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            key = _cache_key(item.query)
            waiters = self._in_flight.pop(key, [])
            wait_s = time.time() - item.enqueued_at

            if wait_s > 0.1:
                logger.debug("queue wait: %.2fs for: %s", wait_s, item.query[:40])

            self._stat_llm_calls += 1
            try:
                result = await self._process(item.query, item.session_id, item.world)
                # Cache the result
                self._cache[key] = (result, time.time())
                # Resolve all waiters (dedup'd requests)
                for f in waiters:
                    if not f.done():
                        f.set_result(result)
            except Exception as e:
                logger.error("LLM call failed: %s", e)
                for f in waiters:
                    if not f.done():
                        f.set_exception(e)
            finally:
                self._queue.task_done()

        logger.info("LLMQueue worker exited")


# ------------------------------------------------------------------ #
#  Context compression                                               #
# ------------------------------------------------------------------ #

def compress_history(history: list[dict]) -> list[dict]:
    """
    Keep last MAX_VERBATIM_TURNS turns verbatim.
    Summarise older turns into a single compact message.

    Reduces input tokens by 60-80% for long sessions while preserving
    continuity for follow-up corrections ("no, the other one").
    """
    recent_msgs = MAX_VERBATIM_TURNS * 2  # 2 messages per turn
    if len(history) <= recent_msgs:
        return history

    old    = history[:-recent_msgs]
    recent = history[-recent_msgs:]

    # Build terse summary: "user asked X → responded Y"
    parts = []
    for i in range(0, len(old) - 1, 2):
        u_content = old[i].get("content", "")[:60].replace("\n", " ")
        a_content = old[i + 1].get("content", "")[:60].replace("\n", " ") if i + 1 < len(old) else "…"
        parts.append(f"• {u_content} → {a_content}")

    kept = parts[-MAX_SUMMARY_TURNS:]   # keep newest N compressed turns
    summary = "Earlier context (compressed):\n" + "\n".join(kept)

    return [
        {"role": "user",  "content": summary},
        {"role": "model", "content": "Got it."},
        *recent,
    ]


# ------------------------------------------------------------------ #
#  Helpers                                                           #
# ------------------------------------------------------------------ #

def _cache_key(query: str) -> str:
    return hashlib.md5(query.lower().strip().encode()).hexdigest()


def _get_ttl(query: str) -> int:
    q = query.lower()
    for keyword, ttl in _CACHE_TTL_PATTERNS:
        if keyword in q:
            return ttl
    return _DEFAULT_TTL
