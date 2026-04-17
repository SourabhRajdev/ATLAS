"""Clipboard source — detects interesting new clipboard content.

Emits:
  - clipboard_url
  - clipboard_tracking_number
  - clipboard_code_snippet (>10 lines, looks like code)

Privacy: never stores raw content. Only payload keys are type and a short hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re

from atlas.autonomy.models import Signal

logger = logging.getLogger("atlas.autonomy.clipboard")

_URL_RX = re.compile(r"^https?://\S+$")
_TRACKING_RX = re.compile(r"^\b(?:1Z[0-9A-Z]{16}|[0-9]{12,22})\b$")


class ClipboardSource:
    source = "clipboard"

    def __init__(self) -> None:
        self._last_hash: str = ""

    async def poll(self) -> list[Signal]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pbpaste",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
            if proc.returncode != 0:
                return []
        except Exception:
            return []

        content = stdout.decode("utf-8", "replace").strip()
        if not content:
            return []
        digest = hashlib.sha1(content.encode()).hexdigest()[:12]
        if digest == self._last_hash:
            return []
        self._last_hash = digest

        kind = None
        payload: dict = {"hash": digest, "length": len(content)}
        first = content.splitlines()[0] if content else ""
        if _URL_RX.match(first):
            kind = "clipboard_url"
            payload["url"] = first
        elif _TRACKING_RX.match(first):
            kind = "clipboard_tracking_number"
            payload["number"] = first
        elif content.count("\n") > 10 and any(
            tok in content for tok in ("def ", "function ", "class ", "{", "=>", "import ")
        ):
            kind = "clipboard_code_snippet"

        if kind is None:
            return []
        return [Signal(self.source, kind, payload)]
