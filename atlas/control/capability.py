"""Capability gate + undo log.

Every action is checked against the user's granted capabilities. Irreversible
or high-risk actions require confirmation. An undo token is written BEFORE the
action executes so a crash mid-action still leaves a reversible trail.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Callable

from atlas.control.models import Action, Capability, UndoToken

logger = logging.getLogger("atlas.control.capability")

UNDO_LOG = Path("~/.atlas/undo.jsonl").expanduser()
GRANTS_FILE = Path("~/.atlas/capabilities.json").expanduser()


DEFAULT_GRANTS: set[Capability] = {
    Capability.READ_FS,
    Capability.READ_MAIL,
    Capability.READ_CALENDAR,
    Capability.SEND_NOTIFICATION,
}


class CapabilityGate:
    """Enforces which capabilities ATLAS is allowed to exercise."""

    def __init__(self, confirm_fn: Callable[[Action], bool] | None = None) -> None:
        self._granted: set[Capability] = self._load_grants()
        self._confirm = confirm_fn or (lambda a: False)

    def _load_grants(self) -> set[Capability]:
        if not GRANTS_FILE.exists():
            GRANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            GRANTS_FILE.write_text(json.dumps([c.value for c in DEFAULT_GRANTS]))
            return set(DEFAULT_GRANTS)
        try:
            raw = json.loads(GRANTS_FILE.read_text())
            return {Capability(v) for v in raw if v in Capability._value2member_map_}
        except Exception as e:
            logger.warning("bad capabilities file, using defaults: %s", e)
            return set(DEFAULT_GRANTS)

    def grant(self, cap: Capability) -> None:
        self._granted.add(cap)
        GRANTS_FILE.write_text(json.dumps([c.value for c in self._granted]))

    def revoke(self, cap: Capability) -> None:
        self._granted.discard(cap)
        GRANTS_FILE.write_text(json.dumps([c.value for c in self._granted]))

    def granted(self) -> set[Capability]:
        return set(self._granted)

    def check(self, action: Action) -> tuple[bool, str]:
        """Returns (allowed, reason)."""
        missing = [c for c in action.capabilities if c not in self._granted]
        if missing:
            return False, f"missing capability: {','.join(c.value for c in missing)}"
        if action.requires_confirmation or not action.reversible:
            if not self._confirm(action):
                return False, "user declined confirmation"
        return True, ""


class UndoLog:
    """Append-only JSONL log of reversible actions."""

    def __init__(self, path: Path = UNDO_LOG) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, action: Action, backend: str, data: dict) -> UndoToken:
        token = UndoToken(
            id=uuid.uuid4().hex[:12],
            action_id=action.id,
            backend=backend,
            kind=action.kind,
            data=data,
        )
        with self.path.open("a") as f:
            f.write(json.dumps({
                "id": token.id,
                "action_id": token.action_id,
                "backend": token.backend,
                "kind": token.kind,
                "data": token.data,
                "created_at": token.created_at,
            }) + "\n")
        return token

    def find(self, token_id: str) -> UndoToken | None:
        if not self.path.exists():
            return None
        for line in self.path.read_text().splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("id") == token_id:
                return UndoToken(**obj)
        return None

    def recent(self, limit: int = 20) -> list[UndoToken]:
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(UndoToken(**json.loads(line)))
            except Exception:
                pass
        return out
