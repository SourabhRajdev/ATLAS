"""Core module tests.

Run: python3 -m atlas.core.tests
"""

from __future__ import annotations

import asyncio
import sys
from atlas.core.llm_queue import compress_history, MAX_VERBATIM_TURNS, MAX_SUMMARY_TURNS

_PASS = 0
_FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f" | {detail}" if detail else ""))


async def run_tests() -> None:
    print("=" * 60)
    print("Core Module Test Suite")
    print("=" * 60)

    # ── Test 1: compress_history ─────────────────────────────────────────
    print("\n[1] Context Compression (compress_history)")

    # 1.1 Short history (no compression)
    short_history = [
        {"role": "user", "content": "hello"},
        {"role": "model", "content": "hi"}
    ]
    compressed_short = compress_history(short_history)
    check("short history unchanged", compressed_short == short_history)

    # 1.2 Boundary history (no compression)
    boundary_history = []
    for i in range(MAX_VERBATIM_TURNS):
        boundary_history.append({"role": "user", "content": f"u{i}"})
        boundary_history.append({"role": "model", "content": f"a{i}"})

    compressed_boundary = compress_history(boundary_history)
    check("boundary history unchanged", compressed_boundary == boundary_history)

    # 1.3 Long history (compression)
    long_history = []
    for i in range(MAX_VERBATIM_TURNS + 1):
        long_history.append({"role": "user", "content": f"u{i}"})
        long_history.append({"role": "model", "content": f"a{i}"})

    compressed_long = compress_history(long_history)
    # original: (MAX_VERBATIM_TURNS + 1) * 2 messages
    # compressed: summary(user) + "Got it."(model) + MAX_VERBATIM_TURNS * 2 messages
    # If MAX_VERBATIM_TURNS=3, original=8, compressed=2+6=8. Length stays same but content changes.
    check("long history compressed (content changed)", compressed_long != long_history)
    check("compressed history has summary user message", compressed_long[0]["role"] == "user")
    check("compressed history has 'Got it.' model message", compressed_long[1] == {"role": "model", "content": "Got it."})
    check("recent messages preserved", compressed_long[2:] == long_history[-MAX_VERBATIM_TURNS*2:])
    check("summary contains earlier turns", "u0 → a0" in compressed_long[0]["content"])

    # 1.4 Very long history (MAX_SUMMARY_TURNS limit)
    very_long_history = []
    num_turns = MAX_VERBATIM_TURNS + MAX_SUMMARY_TURNS + 2
    for i in range(num_turns):
        very_long_history.append({"role": "user", "content": f"u{i}"})
        very_long_history.append({"role": "model", "content": f"a{i}"})

    compressed_very_long = compress_history(very_long_history)
    summary_content = compressed_very_long[0]["content"]
    check("summary honors MAX_SUMMARY_TURNS", summary_content.count("•") == MAX_SUMMARY_TURNS)

    # Expected kept turns in summary:
    # Verbatim takes last MAX_VERBATIM_TURNS turns.
    # Summary takes last MAX_SUMMARY_TURNS from the remaining.
    # e.g. num_turns=10, MAX_VERBATIM_TURNS=3, MAX_SUMMARY_TURNS=5
    # Verbatim: 7, 8, 9
    # Remaining: 0, 1, 2, 3, 4, 5, 6
    # Summary (last 5 of remaining): 2, 3, 4, 5, 6
    idx_first_kept = num_turns - MAX_VERBATIM_TURNS - MAX_SUMMARY_TURNS
    check("summary dropped oldest turns", f"u{idx_first_kept-1} →" not in summary_content)
    check("summary kept most recent old turns", f"u{idx_first_kept} →" in summary_content)

    # 1.5 Content trimming and newline replacement
    long_content = "x" * 100
    special_history = [
        {"role": "user", "content": "line1\nline2"},
        {"role": "model", "content": long_content},
        *boundary_history
    ]
    compressed_special = compress_history(special_history)
    summary_special = compressed_special[0]["content"]
    check("newlines replaced in summary", "line1 line2" in summary_special)
    check("content trimmed in summary", long_content[:60] in summary_special and long_content[:61] not in summary_special)

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
