"""System modes — controls autonomy level."""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger("atlas.modes")


class SystemMode(str, Enum):
    """System operation modes."""
    PASSIVE = "passive"        # Only respond to direct requests
    ASSISTIVE = "assistive"    # Suggest + notify proactively
    AUTONOMOUS = "autonomous"  # Act + notify proactively


class ModeController:
    """Controls system autonomy mode."""
    
    def __init__(self, default_mode: SystemMode = SystemMode.ASSISTIVE) -> None:
        self.current_mode = default_mode
        logger.info("Mode controller initialized: %s", self.current_mode.value)

    def set_mode(self, mode: SystemMode) -> None:
        """Change system mode."""
        old_mode = self.current_mode
        self.current_mode = mode
        logger.info("Mode changed: %s → %s", old_mode.value, mode.value)

    def get_mode(self) -> SystemMode:
        """Get current mode."""
        return self.current_mode

    def should_run_autonomy_loop(self) -> bool:
        """Check if autonomy loop should run in current mode."""
        return self.current_mode in (SystemMode.ASSISTIVE, SystemMode.AUTONOMOUS)

    def should_notify_proactively(self) -> bool:
        """Check if system should send proactive notifications."""
        return self.current_mode in (SystemMode.ASSISTIVE, SystemMode.AUTONOMOUS)

    def should_act_autonomously(self) -> bool:
        """Check if system should take autonomous actions."""
        return self.current_mode == SystemMode.AUTONOMOUS

    def get_confidence_threshold(self) -> float:
        """Get confidence threshold for autonomous actions."""
        if self.current_mode == SystemMode.AUTONOMOUS:
            return 0.7  # Act if 70%+ confident
        elif self.current_mode == SystemMode.ASSISTIVE:
            return 0.9  # Only act if 90%+ confident (rare)
        else:
            return 1.0  # Never act autonomously in passive mode

    def describe_mode(self) -> str:
        """Get human-readable mode description."""
        descriptions = {
            SystemMode.PASSIVE: "Passive — Only responds to direct requests",
            SystemMode.ASSISTIVE: "Assistive — Suggests and notifies proactively",
            SystemMode.AUTONOMOUS: "Autonomous — Acts and notifies proactively",
        }
        return descriptions[self.current_mode]
