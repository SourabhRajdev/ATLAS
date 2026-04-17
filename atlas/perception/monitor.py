"""App + AX monitor — NSWorkspace front app + Accessibility tree extraction.

Lazy imports pyobjc so the module can load on non-mac for tests.
On mac, the daemon polls the front app cheaply (~10ms) and reads
window title / document / URL via AX when available.

For full event-driven mode, see daemon.py — this file is the cheap polling
backend used both by daemon and as a fallback when AX permission is missing.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from atlas.perception.world_state import WorldState

logger = logging.getLogger("atlas.perception.monitor")

# Lazy pyobjc — only on mac, only when first used
_NSWorkspace = None
_kAXFocusedWindowAttribute = None
_AXUIElementCreateApplication = None
_AXUIElementCopyAttributeValue = None


def _ensure_pyobjc() -> bool:
    global _NSWorkspace, _AXUIElementCreateApplication
    global _AXUIElementCopyAttributeValue, _kAXFocusedWindowAttribute
    if _NSWorkspace is not None:
        return True
    try:
        import AppKit  # type: ignore
        from ApplicationServices import (  # type: ignore
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
            kAXFocusedWindowAttribute,
        )
        _NSWorkspace = AppKit.NSWorkspace.sharedWorkspace()
        _AXUIElementCreateApplication = AXUIElementCreateApplication
        _AXUIElementCopyAttributeValue = AXUIElementCopyAttributeValue
        _kAXFocusedWindowAttribute = kAXFocusedWindowAttribute
        return True
    except Exception as e:
        logger.warning("pyobjc unavailable — perception will be limited: %s", e)
        return False


class AppMonitor:
    """Polls the active app + window title + document/URL via AX.

    Designed to be called from PerceptionDaemon every focus event, or as
    a fallback poll every 1-2s when event subscription isn't wired.
    """

    def __init__(self) -> None:
        self.available = _ensure_pyobjc()

    def snapshot(self, into: WorldState | None = None) -> WorldState:
        ws = into or WorldState()
        ws.timestamp = time.time()

        if not self.available:
            return ws

        try:
            front = _NSWorkspace.frontmostApplication()
            if front is None:
                return ws
            ws.active_app = str(front.localizedName() or "")
            ws.active_app_bundle = str(front.bundleIdentifier() or "")
            pid = int(front.processIdentifier())

            ws.push_recent(ws.active_app)

            # AX query — gracefully degrade if accessibility permission missing
            try:
                ax_app = _AXUIElementCreateApplication(pid)
                _, window = _AXUIElementCopyAttributeValue(
                    ax_app, _kAXFocusedWindowAttribute, None,
                )
                if window is not None:
                    title, doc, url = self._read_window_attrs(window)
                    if title:
                        ws.active_window_title = title
                    if doc:
                        ws.active_document = Path(doc)
                    if url:
                        ws.active_url = url
            except Exception as ax_err:
                logger.debug("AX read failed: %s", ax_err)

            # Idle seconds via CGEventSourceSecondsSinceLastEventType
            try:
                import Quartz  # type: ignore
                idle = Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateHIDSystemState,
                    Quartz.kCGAnyInputEventType,
                )
                ws.idle_seconds = int(idle)
                ws.user_input_active = idle < 2.0
            except Exception:
                pass

        except Exception as e:
            logger.debug("snapshot failed: %s", e)

        return ws

    @staticmethod
    def _read_window_attrs(window) -> tuple[str | None, str | None, str | None]:
        """Best-effort title, document path, URL via AX."""
        try:
            from ApplicationServices import (  # type: ignore
                AXUIElementCopyAttributeValue,
                kAXTitleAttribute,
                kAXDocumentAttribute,
                kAXURLAttribute,
            )
        except Exception:
            return None, None, None

        def _read(attr):
            try:
                _, val = AXUIElementCopyAttributeValue(window, attr, None)
                return str(val) if val is not None else None
            except Exception:
                return None

        return _read(kAXTitleAttribute), _read(kAXDocumentAttribute), _read(kAXURLAttribute)
