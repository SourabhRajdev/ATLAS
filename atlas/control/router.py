"""ActionRouter — picks the right backend and enforces the safety pipeline.

Pipeline for every action:
  1. CapabilityGate.check   (may prompt the user)
  2. UndoLog.record         (so a crash still leaves a reversible trail)
  3. backend.execute        (in priority order, falling through on recoverable fail)

Priority: AppleScript > Playwright > AX. (MCP + Shortcuts slot in later.)
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from atlas.control.applescript import AppleScriptBackend
from atlas.control.ax_control import AXBackend
from atlas.control.capability import CapabilityGate, UndoLog
from atlas.control.models import Action, Result
from atlas.control.playwright_ctrl import PlaywrightBackend

logger = logging.getLogger("atlas.control.router")


class ActionRouter:
    def __init__(
        self,
        confirm_fn: Callable[[Action], bool] | None = None,
    ) -> None:
        self.gate = CapabilityGate(confirm_fn=confirm_fn)
        self.undo = UndoLog()
        self.applescript = AppleScriptBackend()
        self.playwright = PlaywrightBackend()
        self.ax = AXBackend()
        self._order = [self.applescript, self.playwright, self.ax]

    async def execute(self, action: Action) -> Result:
        t0 = time.time()
        allowed, reason = self.gate.check(action)
        if not allowed:
            return Result(ok=False, error=f"blocked: {reason}", elapsed_ms=_ms(t0))

        for backend in self._order:
            if not backend.can_handle(action):
                continue
            name = backend.__class__.__name__
            logger.info("executing %s via %s", action.kind, name)
            try:
                ok, output, undo_data = await backend.execute(action)
            except Exception as e:
                logger.warning("%s raised: %s — falling through", name, e)
                continue
            if ok:
                token = self.undo.record(action, name, undo_data) if action.reversible else None
                return Result(
                    ok=True,
                    backend=name,
                    output=output,
                    undo_token=token.id if token else None,
                    elapsed_ms=_ms(t0),
                )
            logger.info("%s refused (%s) — falling through", name, output)

        return Result(ok=False, error=f"no backend handled {action.kind}", elapsed_ms=_ms(t0))

    async def shutdown(self) -> None:
        await self.playwright.shutdown()


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)
