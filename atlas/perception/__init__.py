"""Perception layer — the missing sense organ.

Maintains a live WorldState describing what the user is doing right now.
Other subsystems (executor, autonomy, continuity) read from this rather
than guessing from tool histograms.
"""

from atlas.perception.world_state import WorldState
from atlas.perception.daemon import PerceptionDaemon

__all__ = ["WorldState", "PerceptionDaemon"]
