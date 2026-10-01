"""Query rewrite — turns a context-dependent follow-up into a standalone
query before retrieval. "What did she say about it?" is useless for FTS/
semantic search; rewriting it using recent turns gives retrieval something
to actually match against.

Two tiers:
  1. A cheap model via LLMQueue.run_job(lane=LANE_BACKGROUND), when a router
     is available — gives a qualitatively correct standalone rewrite.
  2. T0 regex fallback: detects a referential/pronoun-leading query and,
     when there's a single immediately-preceding user turn to anchor
     against (the "clear antecedent" case), folds that turn's text into the
     query verbatim. No model call, no latency cost, but mechanical — it
     widens the query's vocabulary rather than truly resolving the pronoun.

This runs the LLM tier first when available, T0 as the fallback — the
reverse of a literal "T0 first" reading, but the same "prefer the smart
path, degrade to the mechanical one" pattern Step D's fact extraction
already uses (model first, regex extractor as the offline fallback). A
pure-regex rewrite can't actually resolve "she"/"it" to the right referent;
it can only make a reasonable guess by widening the query, which is why the
model path is tried first whenever one is available.

Falls back to the query verbatim if neither tier applies or succeeds — this
module never raises, and never block on a model call when none is given.

Per docs/decisions/001-context-engine-and-memory.md Section 3.2. Built here
(Step E) as a standalone, directly-testable component; wiring it into the
live Engine/Executor request path is Step F's job.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from atlas.core.llm_queue import LANE_BACKGROUND

if TYPE_CHECKING:
    from atlas.core.llm_queue import LLMQueue
    from atlas.core.model_router import ModelRouter

logger = logging.getLogger("atlas.context.query_rewrite")

# Leading-pronoun / referential patterns — "what about it", "and that",
# "what did she say", "tell me more about them", "why did it happen".
_REFERENTIAL_RE = re.compile(
    r"^(and |so |then |ok |okay )?"
    r"(what about|tell me more about|what did|why did|how did|and what about)?\s*"
    r".*\b(it|that|this|they|them|those|these|she|he|her|him)\b",
    re.IGNORECASE,
)

# Only short follow-ups get treated as referential — a long message is
# unlikely to be a bare context-dependent reference, and more likely to just
# happen to contain one of the words above incidentally.
_MAX_REFERENTIAL_WORDS = 12

REWRITE_SYSTEM_PROMPT = """\
Rewrite the user's latest message into a standalone question or statement
that makes sense without the conversation history, using the recent turns
given to you for context. Output ONLY the rewritten text, nothing else. If
the message is already standalone, output it unchanged. Never answer the
question — only rewrite it.
"""


def _is_referential(query: str) -> bool:
    words = query.strip().split()
    return len(words) <= _MAX_REFERENTIAL_WORDS and bool(_REFERENTIAL_RE.match(query.strip()))


def _last_user_turn(recent_turns: list[dict]) -> str | None:
    for turn in reversed(recent_turns):
        if turn.get("role") == "user":
            content = turn.get("content")
            if content:
                return content
    return None


def _t0_rewrite(query: str, recent_turns: list[dict]) -> str | None:
    """The 'clear antecedent' case: exactly one immediately-preceding user
    turn to anchor against. Returns None if there's nothing to anchor to
    (e.g. this is the first turn in the session) — not an error, just
    nothing for T0 to do."""
    last_turn = _last_user_turn(recent_turns)
    if not last_turn:
        return None
    return f'{query} (context: the user just said "{last_turn}")'


async def _llm_rewrite(
    query: str,
    recent_turns: list[dict],
    llm_queue: "LLMQueue",
    model_router: "ModelRouter",
) -> str | None:
    context_lines = "\n".join(
        f"{t.get('role', 'user')}: {t.get('content', '')}" for t in recent_turns[-6:]
    ) or "(no prior turns)"
    prompt = f"Recent conversation:\n{context_lines}\n\nLatest message: {query}"

    async def _call():
        return await model_router.generate(
            messages=[{"role": "user", "parts": [{"text": prompt}]}],
            tool_defs=[],
            system_prompt=REWRITE_SYSTEM_PROMPT,
        )

    try:
        response = await llm_queue.run_job(_call, lane=LANE_BACKGROUND, label="query_rewrite")
    except Exception as e:
        logger.debug("query rewrite LLM call failed, falling back to T0: %s", e)
        return None
    text = getattr(response, "text", "") if response else ""
    return text.strip() or None


async def rewrite_query(
    query: str,
    recent_turns: list[dict],
    llm_queue: "LLMQueue | None" = None,
    model_router: "ModelRouter | None" = None,
) -> str:
    """Rewrite `query` into a standalone form for retrieval, using
    `recent_turns` (list of {"role", "content"}, most recent last) for
    context. Returns `query` verbatim whenever it already looks standalone,
    or when neither rewrite tier applies or succeeds.
    """
    if not _is_referential(query):
        return query

    if llm_queue is not None and model_router is not None:
        llm_result = await _llm_rewrite(query, recent_turns, llm_queue, model_router)
        if llm_result:
            return llm_result

    t0_result = _t0_rewrite(query, recent_turns)
    return t0_result or query
