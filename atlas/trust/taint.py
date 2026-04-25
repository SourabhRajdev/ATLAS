"""Taint tracking — classify input sources and detect injection attempts.

Taint propagates from the source of a request, not from the tool call itself.
CLEAN = user typed it directly into the CLI.
EXTERNAL = content originated from outside (web, email, iMessage, clipboard).
HOSTILE = injection pattern detected inside the content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum


class TaintLevel(IntEnum):
    CLEAN = 0     # user typed directly, fully trusted
    EXTERNAL = 1  # from outside world — read-only tools OK, writes need review
    HOSTILE = 2   # injection pattern found — block all MEDIUM+ consequence actions


# Patterns that indicate prompt injection attempts.
# Using compiled regexes for speed (health_check must be < 100ms).
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?",
        r"forget\s+(everything|all|your\s+instructions?)",
        r"you\s+are\s+now\s+(a|an|the)\s+",
        r"your\s+new\s+(role|instructions?|persona|task|goal)",
        r"disregard\s+(all\s+)?(previous|prior|your)\s+",
        r"override\s+(your|all)\s+",
        r"as\s+an?\s+ai\s+without\s+(any\s+)?restrictions?",
        r"pretend\s+(you\s+are|to\s+be)\s+",
        r"\[\s*system\s*\]",
        r"<\s*system\s*>",
        r"###\s*system\s*prompt",
        r"begin\s+new\s+session",
        r"jailbreak",
        r"dan\s+mode",
        r"developer\s+mode\s+enabled",
        r"ignore\s+safety",
        r"bypass\s+(safety|restrictions?|filter)",
    ]
]

# Sources mapped to their base taint level
_SOURCE_TAINT: dict[str, TaintLevel] = {
    "user": TaintLevel.CLEAN,
    "voice": TaintLevel.CLEAN,       # user spoke it — still trusted
    "clipboard": TaintLevel.EXTERNAL, # clipboard can contain anything
    "web": TaintLevel.EXTERNAL,
    "email": TaintLevel.EXTERNAL,
    "imessage": TaintLevel.EXTERNAL,
    "github_issue": TaintLevel.EXTERNAL,
    "github_pr": TaintLevel.EXTERNAL,
    "tool_result": TaintLevel.EXTERNAL,  # result of a prior tool is untrusted
    "autonomy": TaintLevel.EXTERNAL,     # autonomous loop input — conservative
    "unknown": TaintLevel.EXTERNAL,
}


@dataclass
class TaintContext:
    source: str = "user"
    level: TaintLevel = TaintLevel.CLEAN
    injection_patterns_found: list[str] = field(default_factory=list)

    @classmethod
    def from_source(cls, source: str, content: str = "") -> "TaintContext":
        base_level = _SOURCE_TAINT.get(source, TaintLevel.EXTERNAL)
        patterns_found: list[str] = []

        if content and base_level >= TaintLevel.EXTERNAL:
            patterns_found = _detect_injection(content)
            if patterns_found:
                base_level = TaintLevel.HOSTILE

        return cls(source=source, level=base_level, injection_patterns_found=patterns_found)

    @classmethod
    def clean(cls) -> "TaintContext":
        return cls(source="user", level=TaintLevel.CLEAN)

    def is_clean(self) -> bool:
        return self.level == TaintLevel.CLEAN

    def is_hostile(self) -> bool:
        return self.level == TaintLevel.HOSTILE

    def merge(self, other: "TaintContext") -> "TaintContext":
        """Merge another context into this one, keeping the highest level."""
        if other.level > self.level:
            new_level = other.level
            new_source = other.source
        else:
            new_level = self.level
            new_source = self.source

        new_patterns = sorted(list(set(self.injection_patterns_found + other.injection_patterns_found)))
        return TaintContext(
            source=new_source,
            level=new_level,
            injection_patterns_found=new_patterns,
        )

    def __str__(self) -> str:
        name = TaintLevel(self.level).name
        if self.injection_patterns_found:
            return f"{name}({self.source}, injections={len(self.injection_patterns_found)})"
        return f"{name}({self.source})"


def _detect_injection(content: str) -> list[str]:
    """Return list of matched injection pattern descriptions."""
    found = []
    for pattern in _INJECTION_PATTERNS:
        m = pattern.search(content)
        if m:
            found.append(m.group(0)[:80])
    return found


def scan_params_for_injection(params: dict) -> list[str]:
    """Scan all string values in params dict for injection patterns."""
    found = []
    for v in params.values():
        if isinstance(v, str) and v:
            found.extend(_detect_injection(v))
        elif isinstance(v, (list, dict)):
            sub = str(v)
            found.extend(_detect_injection(sub))
    return found
