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

from atlas.core.models import EventType

logger = logging.getLogger("atlas.llm_queue")

# Lanes: interactive requests must never queue behind background work.
# Each lane gets its own queue and worker(s) so a slow background call
# cannot occupy the only worker a foreground request is waiting on.
LANE_INTERACTIVE = "interactive"
LANE_BACKGROUND = "background"
_BACKGROUND_CONCURRENCY = 2   # small, preemptible-in-spirit pool; never 1-at-a-time with interactive

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
    future:     asyncio.Future = field(compare=False, default=None)
    query:      str      = field(compare=False, default="")
    session_id: str      = field(compare=False, default="")
    world:      str | None = field(compare=False, default=None)
    # Set instead of (query, session_id, world) for one-off jobs that don't
    # fit the conversational-turn shape (see LLMQueue.run_job).
    job_fn:     Callable[[], Awaitable[Any]] | None = field(compare=False, default=None)


class LLMQueue:
    """LLM request queue with caching, dedup, and context compression.

    Two independent lanes, each with its own queue:
      - interactive: the user is waiting. One worker, FIFO/priority within lane.
      - background:  proactive evaluation, memory extraction, etc. A small
                      concurrent pool, entirely separate from the interactive
                      worker so it can never make a foreground request wait.
    """

    def __init__(self, process_fn: Callable[..., Awaitable[Any]]) -> None:
        """
        process_fn: async (query, session_id, world_summary) -> (response, trace)
        """
        self._process = process_fn
        self._queues: dict[str, asyncio.PriorityQueue[_QueueItem]] = {
            LANE_INTERACTIVE: asyncio.PriorityQueue(),
            LANE_BACKGROUND: asyncio.PriorityQueue(),
        }
        # cache: query_hash -> (result, timestamp)
        self._cache: dict[str, tuple[Any, float]] = {}
        # in-flight dedup: query_hash -> list[Future] waiting for the same result
        self._in_flight: dict[str, list[asyncio.Future]] = {}
        self._running = False
        self._worker_tasks: list[asyncio.Task] = []
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
        self._worker_tasks = [
            asyncio.create_task(self._worker(LANE_INTERACTIVE), name="llm-queue-interactive"),
        ]
        self._worker_tasks += [
            asyncio.create_task(self._worker(LANE_BACKGROUND), name=f"llm-queue-background-{i}")
            for i in range(_BACKGROUND_CONCURRENCY)
        ]
        logger.info("LLMQueue started (1 interactive worker, %d background)", _BACKGROUND_CONCURRENCY)

    def stop(self) -> None:
        self._running = False
        for t in self._worker_tasks:
            if not t.done():
                t.cancel()
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
        lane: str = LANE_INTERACTIVE,
    ) -> Any:
        """Submit a query. Returns (response, trace) when processed.

        `lane` decides which worker pool serves this request. Background
        callers (proactive evaluation, memory extraction) MUST pass
        lane=LANE_BACKGROUND so they never occupy the interactive worker.
        """
        if lane not in self._queues:
            raise ValueError(f"unknown lane: {lane}")
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
        queue = self._queues[lane]
        await queue.put(item)
        logger.debug("queued [%s] (pri=%d, depth=%d): %s", lane, priority, queue.qsize(), query[:40])
        return await fut

    async def run_job(
        self,
        job_fn: Callable[[], Awaitable[Any]],
        *,
        priority: Priority = PRIORITY_NORMAL,
        lane: str = LANE_BACKGROUND,
        label: str = "job",
    ) -> Any:
        """Run a one-off async callable on a lane's worker pool.

        For background model calls that don't fit enqueue()'s conversational
        (query, session_id, world_summary) -> (response, trace) shape — e.g.
        AttentionSystem's per-signal classification call. No caching, no
        dedup (there's no stable query text to key on); the only thing this
        gives you is the lane's concurrency isolation, which is the point:
        a slow background job still can't occupy the interactive worker.
        """
        if lane not in self._queues:
            raise ValueError(f"unknown lane: {lane}")
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        item = _QueueItem(
            priority=priority,
            enqueued_at=time.time(),
            future=fut,
            job_fn=job_fn,
        )
        queue = self._queues[lane]
        await queue.put(item)
        logger.debug("queued [%s] (pri=%d, depth=%d): job:%s", lane, priority, queue.qsize(), label)
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
            "queue_depth": sum(q.qsize() for q in self._queues.values()),
            "queue_depth_interactive": self._queues[LANE_INTERACTIVE].qsize(),
            "queue_depth_background":  self._queues[LANE_BACKGROUND].qsize(),
        }

    # ------------------------------------------------------------------ #
    #  Worker (one per lane-slot; interactive and background never share) #
    # ------------------------------------------------------------------ #

    async def _worker(self, lane: str) -> None:
        queue = self._queues[lane]
        logger.info("LLMQueue worker running (%s)", lane)
        while self._running:
            # Poll with timeout so we can exit cleanly
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            wait_s = time.time() - item.enqueued_at

            # One-off job (run_job) — no cache/dedup bookkeeping, just run it
            # in isolation on this lane's worker.
            if item.job_fn is not None:
                if wait_s > 0.1:
                    logger.debug("queue wait [%s]: %.2fs for job", lane, wait_s)
                self._stat_llm_calls += 1
                try:
                    result = await item.job_fn()
                    if not item.future.done():
                        item.future.set_result(result)
                except Exception as e:
                    logger.error("background job failed: %s", e)
                    if not item.future.done():
                        item.future.set_exception(e)
                finally:
                    queue.task_done()
                continue

            key = _cache_key(item.query)
            waiters = self._in_flight.pop(key, [])

            if wait_s > 0.1:
                logger.debug("queue wait [%s]: %.2fs for: %s", lane, wait_s, item.query[:40])

            self._stat_llm_calls += 1
            try:
                result = await self._process(item.query, item.session_id, item.world)
                # Only cache responses with no tool calls. A cached response
                # that actually ran a tool (volume up, send it, next track)
                # would silently skip re-running that tool's side effect the
                # next time the same text comes in within the TTL window.
                if _is_cacheable(result):
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
                queue.task_done()

        logger.info("LLMQueue worker exited (%s)", lane)


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

def _is_cacheable(result: Any) -> bool:
    """A response is safe to cache only if producing it had no side effects.

    `result` is whatever `process_fn` returns — normally (response_text, trace)
    where trace.observations is a list of Events. If any observation is a
    TOOL_CALL, re-serving this exact result for an identical later query would
    silently skip re-executing that tool (e.g. "volume up" called once, then
    answered from cache on repeat with the volume never actually changing).
    """
    if not isinstance(result, tuple) or len(result) != 2:
        return True
    _, trace = result
    observations = getattr(trace, "observations", None)
    if observations is None:
        return True
    return not any(getattr(ev, "type", None) == EventType.TOOL_CALL for ev in observations)


def _cache_key(query: str) -> str:
    return hashlib.md5(query.lower().strip().encode()).hexdigest()


def _get_ttl(query: str) -> int:
    q = query.lower()
    for keyword, ttl in _CACHE_TTL_PATTERNS:
        if keyword in q:
            return ttl
    return _DEFAULT_TTL
