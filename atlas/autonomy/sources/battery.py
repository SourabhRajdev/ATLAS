"""Battery source — pmset -g batt parsing.

Emits:
  - battery_low (<= 15% on battery)
  - battery_critical (<= 5% on battery)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from atlas.autonomy.models import Signal

logger = logging.getLogger("atlas.autonomy.battery")

_RX = re.compile(r"(\d+)%;\s*(\w+)")


class BatterySource:
    source = "battery"

    def __init__(self) -> None:
        self._last_fired: dict[str, float] = {}

    async def poll(self) -> list[Signal]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pmset", "-g", "batt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode != 0:
                return []
        except Exception as e:
            logger.debug("pmset failed: %s", e)
            return []

        text = stdout.decode("utf-8", "replace")
        m = _RX.search(text)
        if not m:
            return []
        pct = int(m.group(1))
        state = m.group(2).lower()
        if "discharging" not in state and "battery" not in text.lower().split("'")[1:2]:
            # Plugged in — no signal
            if "AC Power" in text:
                return []

        signals: list[Signal] = []
        now = time.time()
        if pct <= 5 and now - self._last_fired.get("critical", 0) > 600:
            self._last_fired["critical"] = now
            signals.append(Signal(self.source, "battery_critical", {"percent": pct}))
        elif pct <= 15 and now - self._last_fired.get("low", 0) > 1800:
            self._last_fired["low"] = now
            signals.append(Signal(self.source, "battery_low", {"percent": pct}))
        return signals
