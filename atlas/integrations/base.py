"""Base class for all Atlas integrations."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class IntegrationHealth:
    name: str
    status: str          # "healthy" | "degraded" | "down" | "disabled"
    last_poll: float = 0.0
    last_success: float = 0.0
    error: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "last_poll": self.last_poll,
            "last_success": self.last_success,
            "error": self.error,
            "details": self.details,
        }


class BaseIntegration(ABC):
    name: str = "unknown"
    enabled: bool = True

    def __init__(self) -> None:
        self._health = IntegrationHealth(name=self.name, status="down")

    @abstractmethod
    async def poll(self) -> list[dict]:
        """Fetch new items since last poll. Returns list of event dicts."""

    @abstractmethod
    def health_check(self) -> IntegrationHealth:
        """Return current health state."""

    def _ok(self, details: dict | None = None) -> None:
        now = time.time()
        self._health.status = "healthy"
        self._health.last_poll = now
        self._health.last_success = now
        self._health.error = ""
        if details:
            self._health.details.update(details)

    def _fail(self, error: str) -> None:
        self._health.status = "degraded" if self._health.last_success > 0 else "down"
        self._health.last_poll = time.time()
        self._health.error = error
