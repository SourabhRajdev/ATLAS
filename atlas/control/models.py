"""Control layer data types."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Capability(str, Enum):
    READ_FS = "read_fs"
    WRITE_FS = "write_fs"
    READ_MAIL = "read_mail"
    SEND_MAIL = "send_mail"
    READ_CALENDAR = "read_calendar"
    WRITE_CALENDAR = "write_calendar"
    CONTROL_BROWSER = "control_browser"
    CONTROL_APP = "control_app"
    RUN_SHELL = "run_shell"
    SEND_NOTIFICATION = "send_notification"
    CLICK_UI = "click_ui"


@dataclass
class Action:
    """A single intended effect on the world."""
    kind: str                       # e.g. "mail.draft", "calendar.create", "browser.open"
    params: dict[str, Any] = field(default_factory=dict)
    capabilities: list[Capability] = field(default_factory=list)
    reversible: bool = True
    requires_confirmation: bool = False
    rationale: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class Result:
    ok: bool
    backend: str = ""
    output: Any = None
    error: str = ""
    undo_token: str | None = None
    elapsed_ms: int = 0


@dataclass
class UndoToken:
    id: str
    action_id: str
    backend: str
    kind: str
    data: dict[str, Any]            # everything needed to reverse
    created_at: float = field(default_factory=time.time)
