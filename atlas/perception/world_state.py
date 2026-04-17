"""WorldState — the central truth model of what the user is doing.

This is the ground truth other subsystems read from. Updated by the
PerceptionDaemon on focus-change events. Cheap to read, cheap to copy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path


CODING_APPS = {
    "Cursor", "Xcode", "Code", "Visual Studio Code", "VS Code",
    "Terminal", "iTerm2", "PyCharm", "WebStorm", "IntelliJ IDEA",
    "Sublime Text", "Nova", "Zed",
}

BROWSER_APPS = {
    "Safari", "Google Chrome", "Chrome", "Firefox", "Arc", "Brave Browser",
    "Microsoft Edge", "Vivaldi",
}

MEETING_APPS = {
    "zoom.us", "Zoom", "Microsoft Teams", "Google Meet", "FaceTime",
    "Webex", "Slack",  # huddles
}

WRITING_APPS = {
    "Notion", "Obsidian", "Bear", "Notes", "iA Writer", "Ulysses",
    "Microsoft Word", "Pages", "Google Docs",
}


@dataclass
class WorldState:
    timestamp: float = field(default_factory=time.time)
    active_app: str = ""
    active_app_bundle: str = ""
    active_window_title: str = ""
    active_document: Path | None = None
    active_url: str | None = None
    recent_apps: list[tuple[str, float]] = field(default_factory=list)  # (name, ts)
    idle_seconds: int = 0
    user_input_active: bool = False
    last_screenshot_path: Path | None = None
    last_ocr_text: str = ""
    last_vlm_summary: str | None = None
    cursor_context: str | None = None  # selected text or current line

    # ----- predicates -----

    def is_idle(self) -> bool:
        return self.idle_seconds > 60

    def is_coding(self) -> bool:
        return self.active_app in CODING_APPS

    def is_browsing(self) -> bool:
        return self.active_app in BROWSER_APPS

    def is_in_meeting(self) -> bool:
        return self.active_app in MEETING_APPS

    def is_writing(self) -> bool:
        return self.active_app in WRITING_APPS

    # ----- summary for LLM grounding -----

    def to_summary(self) -> str:
        """Compact one-paragraph summary fed into the executor as ground truth."""
        bits = []
        if self.active_app:
            bits.append(f"App: {self.active_app}")
        if self.active_window_title:
            bits.append(f"Window: {self.active_window_title}")
        if self.active_document:
            bits.append(f"Document: {self.active_document}")
        if self.active_url:
            bits.append(f"URL: {self.active_url}")
        if self.is_idle():
            bits.append(f"User idle {self.idle_seconds}s")
        elif self.user_input_active:
            bits.append("User actively typing/clicking")
        if self.is_in_meeting():
            bits.append("IN MEETING — minimize interruptions")
        if self.cursor_context:
            bits.append(f"Cursor: {self.cursor_context[:120]}")
        return " | ".join(bits) if bits else "(no perception data)"

    def to_display(self) -> str:
        """Clean multi-line output for /world command."""
        lines = []
        app_line = self.active_app or "unknown"
        if self.active_app_bundle:
            app_line += f"  ({self.active_app_bundle})"
        lines.append(f"App        {app_line}")
        lines.append(f"Window     {self.active_window_title or 'unknown'}")
        lines.append(f"Document   {self.active_document or 'none'}")
        if self.active_url:
            lines.append(f"URL        {self.active_url}")
        idle_tag = "(active)" if self.user_input_active else "(idle)" if self.is_idle() else ""
        lines.append(f"Idle       {self.idle_seconds}s {idle_tag}")
        if self.is_in_meeting():
            lines.append("Meeting    IN MEETING")
        if self.last_ocr_text:
            lines.append(f"OCR        {len(self.last_ocr_text)} chars")
        return "\n".join(lines)

    def push_recent(self, app: str, max_age_s: int = 1200) -> None:
        """Append app to recent trail, prune anything older than 20 min."""
        now = time.time()
        self.recent_apps.append((app, now))
        cutoff = now - max_age_s
        self.recent_apps = [(a, t) for a, t in self.recent_apps if t > cutoff][-50:]
