"""Trust classifier — combines taint + consequence + hard limits into one Decision.

This is the single function the rest of the system calls.
All three layers are evaluated in sequence; first block wins.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.trust.consequences import Consequence, classify as classify_consequence
from atlas.trust.hard_limits import check as check_hard_limits
from atlas.trust.taint import TaintContext, TaintLevel, scan_params_for_injection


@dataclass
class Decision:
    allowed: bool
    reason: str
    consequence: Consequence
    requires_confirm: bool = False   # trust layer can escalate AUTO→CONFIRM
    blocked_by: str | None = None    # "hard_limit" | "taint" | "injection_in_params"


class TrustClassifier:
    """Stateless — create once, call evaluate() repeatedly."""

    def evaluate(
        self,
        tool_name: str,
        params: dict,
        taint: TaintContext,
    ) -> Decision:
        consequence = classify_consequence(tool_name, params)

        # --- Layer 1: Hard limits (unconditional, no override) ---
        hard_block = check_hard_limits(tool_name, params)
        if hard_block:
            return Decision(
                allowed=False,
                reason=hard_block,
                consequence=consequence,
                blocked_by="hard_limit",
            )

        # --- Layer 2: Injection patterns inside params themselves ---
        # Even a CLEAN-tainted request can carry injected content in params.
        # e.g. user pastes email content as a write_file argument.
        if consequence >= Consequence.MEDIUM:
            injections = scan_params_for_injection(params)
            if injections:
                return Decision(
                    allowed=False,
                    reason=f"injection pattern in params: {injections[0]!r}",
                    consequence=consequence,
                    blocked_by="injection_in_params",
                )

        # --- Layer 3: Taint × consequence policy ---
        return _taint_policy(tool_name, taint, consequence)


def _taint_policy(
    tool_name: str,
    taint: TaintContext,
    consequence: Consequence,
) -> Decision:
    """
    Policy matrix:

                         BENIGN  LOW   MEDIUM  HIGH   CRITICAL
    CLEAN (user typed)   allow   allow allow   allow  allow
    EXTERNAL (web/email) allow   allow confirm block  block
    HOSTILE (injection)  allow   block block   block  block

    "confirm" means: escalate to CONFIRM tier even if the tool is AUTO/NOTIFY.
    """
    level = taint.level

    if level == TaintLevel.HOSTILE:
        if consequence >= Consequence.LOW:
            return Decision(
                allowed=False,
                reason=(
                    f"hostile taint (injection detected from {taint.source!r}) "
                    f"blocks {consequence.name} action"
                ),
                consequence=consequence,
                blocked_by="taint",
            )
        # BENIGN under hostile taint is allowed (read-only tools are safe)
        return Decision(
            allowed=True,
            reason=f"hostile taint but consequence is {consequence.name}",
            consequence=consequence,
        )

    if level == TaintLevel.EXTERNAL:
        if consequence >= Consequence.HIGH:
            return Decision(
                allowed=False,
                reason=(
                    f"external taint ({taint.source!r}) blocks "
                    f"{consequence.name}-consequence action: {tool_name}"
                ),
                consequence=consequence,
                blocked_by="taint",
            )
        if consequence == Consequence.MEDIUM:
            return Decision(
                allowed=True,
                reason=f"external taint escalates {tool_name} to CONFIRM",
                consequence=consequence,
                requires_confirm=True,
            )
        # LOW or BENIGN under external taint → allow
        return Decision(
            allowed=True,
            reason=f"external taint, low consequence: {tool_name}",
            consequence=consequence,
        )

    # CLEAN taint → allow everything (existing tier system handles CONFIRM)
    return Decision(
        allowed=True,
        reason="clean",
        consequence=consequence,
    )
