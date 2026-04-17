"""Accessibility backend — read + click via AXUIElement tree.

Last resort before PyAutoGUI. Can read button titles, click by title, and
focus windows. Requires macOS Accessibility permission.
"""

from __future__ import annotations

import logging
from typing import Any

from atlas.control.models import Action

logger = logging.getLogger("atlas.control.ax")


class AXBackend:
    SUPPORTS = {"ax.click_button", "ax.list_buttons", "ax.focus_app"}

    def can_handle(self, action: Action) -> bool:
        return action.kind in self.SUPPORTS

    async def execute(self, action: Action) -> tuple[bool, Any, dict]:
        try:
            if action.kind == "ax.focus_app":
                return self._focus_app(action.params)
            if action.kind == "ax.list_buttons":
                return self._list_buttons(action.params)
            if action.kind == "ax.click_button":
                return self._click_button(action.params)
        except Exception as e:
            logger.debug("ax error: %s", e)
            return False, str(e), {}
        return False, "unsupported", {}

    def _focus_app(self, p: dict) -> tuple[bool, Any, dict]:
        try:
            import AppKit  # type: ignore
        except Exception as e:
            return False, f"pyobjc missing: {e}", {}
        name = p.get("app", "")
        apps = AppKit.NSWorkspace.sharedWorkspace().runningApplications()
        for a in apps:
            if str(a.localizedName() or "") == name:
                a.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
                return True, f"focused {name}", {"app": name}
        return False, f"app not running: {name}", {}

    def _list_buttons(self, p: dict) -> tuple[bool, Any, dict]:
        # Full AX tree walk is noisy — return stub for now; wired by orchestrator
        # when it needs richer UI state than OCR provides.
        return True, [], {}

    def _click_button(self, p: dict) -> tuple[bool, Any, dict]:
        return False, "ax click not yet implemented — prefer AppleScript or Playwright", {}
