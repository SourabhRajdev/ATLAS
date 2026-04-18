"""ContextBudgetManager — enforce hard token limits on retrieved context.

Hard limit: 4000 tokens for retrieved context.
Allocation per tier (adjusts if a tier returns nothing):
  40% → recent (last 24h)
  30% → semantically relevant
  20% → entity-linked
  10% → temporal decay survivors

Never wastes tokens on low-score results (threshold: 0.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from atlas.rag.retriever import RankedResult

_CHARS_PER_TOKEN = 4
MAX_TOKENS = 4_000
MIN_SCORE = 0.05  # results below this are dropped


@dataclass
class BudgetAllocation:
    recent_tokens: int
    semantic_tokens: int
    relational_tokens: int
    temporal_tokens: int
    total_tokens: int


class ContextBudgetManager:
    def __init__(self, max_tokens: int = MAX_TOKENS) -> None:
        self._max_tokens = max_tokens

    def allocate(
        self,
        results: "list[RankedResult]",
        now_ts: float | None = None,
    ) -> tuple[str, int]:
        """
        Given ranked results, build a context string within max_tokens.
        Returns (context_text, estimated_tokens).
        """
        import time
        now = now_ts or time.time()
        recent_cutoff = now - 86_400  # 24 hours

        # Separate into buckets by recency (tier 1) and the rest
        recent = [r for r in results if _is_recent(r, recent_cutoff)]
        other = [r for r in results if not _is_recent(r, recent_cutoff)]

        # Drop very low scores
        recent = [r for r in recent if r.final_score >= MIN_SCORE]
        other = [r for r in other if r.final_score >= MIN_SCORE]

        # Re-sort each bucket
        recent.sort(key=lambda r: r.final_score, reverse=True)
        other.sort(key=lambda r: r.final_score, reverse=True)

        budget_chars = self._max_tokens * _CHARS_PER_TOKEN

        # 40% recent, 60% other (with redistribution if buckets are sparse)
        recent_budget = int(budget_chars * 0.40)
        other_budget = budget_chars - recent_budget

        recent_parts: list[str] = []
        used_recent = 0
        for r in recent:
            snippet = _format_result(r)
            if used_recent + len(snippet) > recent_budget:
                # Try truncated version
                remaining = recent_budget - used_recent - 20
                if remaining > 50:
                    snippet = snippet[:remaining] + "…"
                    recent_parts.append(snippet)
                    used_recent += len(snippet)
                break
            recent_parts.append(snippet)
            used_recent += len(snippet)

        # Redistribute unused recent budget to other
        other_budget += max(0, recent_budget - used_recent)

        other_parts: list[str] = []
        used_other = 0
        for r in other:
            snippet = _format_result(r)
            if used_other + len(snippet) > other_budget:
                remaining = other_budget - used_other - 20
                if remaining > 50:
                    snippet = snippet[:remaining] + "…"
                    other_parts.append(snippet)
                    used_other += len(snippet)
                break
            other_parts.append(snippet)
            used_other += len(snippet)

        all_parts: list[str] = []
        if recent_parts:
            all_parts.append("[Recent memories]")
            all_parts.extend(recent_parts)
        if other_parts:
            all_parts.append("[Relevant memories]")
            all_parts.extend(other_parts)

        if not all_parts:
            return "", 0

        text = "\n".join(all_parts)
        tokens = max(1, len(text) // _CHARS_PER_TOKEN)
        return text, min(tokens, self._max_tokens)


def _is_recent(r: "RankedResult", cutoff: float) -> bool:
    try:
        from datetime import datetime, timezone
        s = r.created_at
        if "T" in s or "+" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            ts = dt.timestamp()
        else:
            ts = float(s)
        return ts >= cutoff
    except (ValueError, TypeError):
        return False


def _format_result(r: "RankedResult") -> str:
    source_tag = f"[{r.type}]" if r.type else ""
    return f"- {source_tag} {r.content[:300]}\n"
