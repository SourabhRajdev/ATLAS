"""Perception daemon — maintains the live WorldState.

Simple cooperative loop:
  - Polls AppMonitor every 0.5s when user is active
  - Throttles to 5s when idle
  - Emits world_state_changed events to subscribers (asyncio.Queue)
  - Captures screenshot on focus change (front window only, throttled)

This is intentionally simpler than a fully event-driven NSWorkspace observer
because event registration via pyobjc is brittle and varies by macOS version.
A 0.5s poll on a single getter is cheap (<1ms) and good enough.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from atlas.perception.monitor import AppMonitor
from atlas.perception.privacy import PrivacyGate, is_app_blacklisted
from atlas.perception.screen import ScreenPipeline
from atlas.perception.world_state import WorldState

logger = logging.getLogger("atlas.perception.daemon")


class PerceptionDaemon:
    def __init__(self) -> None:
        self.monitor = AppMonitor()
        self.screen = ScreenPipeline()
        self.privacy = PrivacyGate()
        self.world = WorldState()
        self._subscribers: list[Callable[[WorldState], None]] = []
        self._running = False
        self._last_focus_app: str = ""
        self._last_screenshot_at: float = 0.0
        self._screenshot_min_interval = 5.0  # don't screenshot more than once per 5s

    def current(self) -> WorldState:
        return self.world

    def subscribe(self, fn: Callable[[WorldState], None]) -> None:
        self._subscribers.append(fn)

    def pause(self, seconds: int = 300) -> None:
        self.privacy.pause(seconds)

    async def run(self) -> None:
        self._running = True
        logger.info("perception daemon started (mac=%s)", self.monitor.available)
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("perception tick error: %s", e)
            # Adaptive poll
            interval = 5.0 if self.world.is_idle() else 0.5
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False

    async def _tick(self) -> None:
        if self.privacy.paused:
            return

        new_state = self.monitor.snapshot()
        new_state.recent_apps = self.world.recent_apps  # preserve trail

        focus_changed = new_state.active_app and new_state.active_app != self._last_focus_app
        if focus_changed:
            self._last_focus_app = new_state.active_app
            new_state.push_recent(new_state.active_app)

            # Screenshot on focus change (throttled, privacy-gated)
            now = time.time()
            if (
                now - self._last_screenshot_at > self._screenshot_min_interval
                and not is_app_blacklisted(new_state.active_app)
                and not new_state.is_idle()
            ):
                self._last_screenshot_at = now
                # Run capture in thread (CG calls block)
                path = await asyncio.to_thread(
                    self.screen.capture_front_window, new_state.active_app,
                )
                if path:
                    new_state.last_screenshot_path = path

        self.world = new_state

        if focus_changed:
            for fn in list(self._subscribers):
                try:
                    fn(new_state)
                except Exception as e:
                    logger.debug("subscriber error: %s", e)
