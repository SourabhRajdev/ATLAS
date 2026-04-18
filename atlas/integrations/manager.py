"""IntegrationManager — coordinates all integrations and polls them on schedule.

Each integration has its own poll interval. The manager runs as a background
asyncio task and delivers events to a registered callback.

Integrations are loaded lazily — only enabled ones are polled.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable

from atlas.integrations.base import BaseIntegration, IntegrationHealth

logger = logging.getLogger("atlas.integrations.manager")

DEFAULT_POLL_INTERVALS: dict[str, float] = {
    "gmail":        5 * 60,    # 5 minutes
    "imessage":     60,        # 1 minute
    "calendar":     60,        # 1 minute
    "apple_health": 30 * 60,   # 30 minutes
}


class IntegrationManager:
    def __init__(
        self,
        data_dir: Path,
        event_callback: Callable[[list[dict]], None] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._callback = event_callback
        self._integrations: dict[str, BaseIntegration] = {}
        self._poll_intervals: dict[str, float] = dict(DEFAULT_POLL_INTERVALS)
        self._last_poll: dict[str, float] = {}
        self._running = False
        self._task: asyncio.Task | None = None

    def register(
        self,
        integration: BaseIntegration,
        poll_interval: float | None = None,
    ) -> None:
        name = integration.name
        self._integrations[name] = integration
        if poll_interval is not None:
            self._poll_intervals[name] = poll_interval
        self._last_poll[name] = 0.0
        logger.info("Registered integration: %s (interval=%.0fs)", name,
                    self._poll_intervals.get(name, 60))

    def register_callback(self, fn: Callable[[list[dict]], None]) -> None:
        self._callback = fn

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="integration_manager")
        logger.info("IntegrationManager started with %d integrations", len(self._integrations))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("IntegrationManager stopped")

    async def _loop(self) -> None:
        while self._running:
            now = time.time()
            for name, integration in list(self._integrations.items()):
                if not integration.enabled:
                    continue
                interval = self._poll_intervals.get(name, 60)
                if now - self._last_poll.get(name, 0.0) >= interval:
                    await self._poll_one(name, integration)
            await asyncio.sleep(10)  # check every 10s whether any integration is due

    async def _poll_one(self, name: str, integration: BaseIntegration) -> None:
        self._last_poll[name] = time.time()
        try:
            events = await integration.poll()
            if events and self._callback:
                try:
                    result = self._callback(events)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error("Event callback failed for %s: %s", name, e)
            if events:
                logger.debug("Integration %s emitted %d events", name, len(events))
        except Exception as e:
            logger.error("Integration %s poll raised: %s", name, e)

    async def poll_all_now(self) -> list[dict]:
        """Force-poll all integrations immediately. Returns all events."""
        all_events: list[dict] = []
        for name, integration in self._integrations.items():
            if not integration.enabled:
                continue
            try:
                events = await integration.poll()
                all_events.extend(events)
            except Exception as e:
                logger.error("Integration %s poll failed: %s", name, e)
        return all_events

    def health_check(self) -> dict:
        integration_health = {}
        overall = "healthy"
        for name, integration in self._integrations.items():
            h = integration.health_check()
            integration_health[name] = h.to_dict()
            if h.status in ("down",) and integration.enabled:
                overall = "degraded"

        return {
            "status": "healthy" if self._running else "down",
            "manager_running": self._running,
            "integrations": integration_health,
            "overall_integration_health": overall,
        }

    @classmethod
    def build_default(
        cls,
        data_dir: Path,
        gmail_credentials: Path | None = None,
        event_callback: Callable[[list[dict]], None] | None = None,
        enable_gmail: bool = True,
        enable_imessage: bool = True,
        enable_health: bool = False,
        enable_calendar: bool = True,
    ) -> "IntegrationManager":
        """Factory: build manager with standard integrations."""
        from atlas.integrations.calendar import CalendarIntegration
        from atlas.integrations.imessage import IMessageIntegration

        manager = cls(data_dir=data_dir, event_callback=event_callback)

        if enable_imessage:
            manager.register(IMessageIntegration(data_dir=data_dir))

        if enable_calendar:
            manager.register(CalendarIntegration())

        if enable_gmail:
            try:
                from atlas.integrations.gmail import GmailIntegration
                manager.register(GmailIntegration(
                    data_dir=data_dir,
                    credentials_file=gmail_credentials,
                ))
            except ImportError:
                logger.warning("Gmail integration disabled: google-api-python-client not installed")

        if enable_health:
            from atlas.integrations.health import AppleHealthIntegration
            manager.register(AppleHealthIntegration(data_dir=data_dir))

        return manager
