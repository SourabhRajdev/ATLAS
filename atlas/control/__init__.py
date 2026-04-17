"""Control layer — how ATLAS actually does things on the machine.

Priority order: MCP > AppleScript > Shortcuts > Playwright > AX > PyAutoGUI.
Every action passes through a capability gate and records an undo token
before execution.
"""

from atlas.control.models import Action, Result, Capability, UndoToken
from atlas.control.router import ActionRouter

__all__ = ["Action", "Result", "Capability", "UndoToken", "ActionRouter"]
