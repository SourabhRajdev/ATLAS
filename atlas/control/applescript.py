"""AppleScript backend — native Mac app control via osascript.

Handles Mail, Calendar, Finder, Safari, Notes, Reminders. Each method returns
(ok, output_or_error). Scripts are composed from parameters with strict
string-quoting to avoid injection.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from atlas.control.models import Action

logger = logging.getLogger("atlas.control.applescript")


def _q(s: str) -> str:
    """Quote a string for embedding into AppleScript."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


async def _run(script: str, timeout: float = 10.0) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return False, stderr.decode("utf-8", "replace").strip()
        return True, stdout.decode("utf-8", "replace").strip()
    except asyncio.TimeoutError:
        return False, "osascript timeout"
    except Exception as e:
        return False, str(e)


class AppleScriptBackend:
    """Dispatch table: action.kind -> coroutine."""

    SUPPORTS = {
        "mail.draft", "mail.send",
        "calendar.create", "calendar.list_today",
        "finder.reveal", "finder.move_to_trash",
        "safari.open_url", "safari.current_url",
        "notes.create",
        "reminders.create",
        "notification.show",
    }

    def can_handle(self, action: Action) -> bool:
        return action.kind in self.SUPPORTS

    async def execute(self, action: Action) -> tuple[bool, Any, dict]:
        """Returns (ok, output, undo_data)."""
        handler = getattr(self, f"_{action.kind.replace('.', '_')}", None)
        if handler is None:
            return False, f"no handler for {action.kind}", {}
        return await handler(action.params)

    # ---------- Mail ----------

    async def _mail_draft(self, p: dict) -> tuple[bool, Any, dict]:
        to = p.get("to", "")
        subject = p.get("subject", "")
        body = p.get("body", "")
        script = f'''
        tell application "Mail"
            set newMsg to make new outgoing message with properties {{subject:{_q(subject)}, content:{_q(body)}, visible:true}}
            tell newMsg
                make new to recipient at end of to recipients with properties {{address:{_q(to)}}}
            end tell
            return id of newMsg as string
        end tell
        '''
        ok, out = await _run(script)
        return ok, out, {"message_id": out} if ok else (ok, out, {})

    async def _mail_send(self, p: dict) -> tuple[bool, Any, dict]:
        to = p.get("to", "")
        subject = p.get("subject", "")
        body = p.get("body", "")
        script = f'''
        tell application "Mail"
            set newMsg to make new outgoing message with properties {{subject:{_q(subject)}, content:{_q(body)}, visible:false}}
            tell newMsg
                make new to recipient at end of to recipients with properties {{address:{_q(to)}}}
                send
            end tell
        end tell
        '''
        ok, out = await _run(script)
        return ok, out, {"to": to, "subject": subject}

    # ---------- Calendar ----------

    async def _calendar_create(self, p: dict) -> tuple[bool, Any, dict]:
        title = p.get("title", "")
        start = p.get("start_iso", "")
        end = p.get("end_iso", "")
        calendar = p.get("calendar", "Calendar")
        script = f'''
        tell application "Calendar"
            tell calendar {_q(calendar)}
                set newEv to make new event with properties {{summary:{_q(title)}, start date:(current date) + 0, end date:(current date) + 3600}}
                return uid of newEv as string
            end tell
        end tell
        '''
        # Note: passing real ISO dates to AppleScript reliably needs `date "M/D/YYYY H:MM AM"` formatting.
        # This stub creates a "+1 hour from now" event — caller should post-edit via another script.
        ok, out = await _run(script)
        return ok, out, {"event_id": out, "start": start, "end": end, "title": title}

    async def _calendar_list_today(self, p: dict) -> tuple[bool, Any, dict]:
        script = '''
        tell application "Calendar"
            set today to current date
            set hours of today to 0
            set minutes of today to 0
            set seconds of today to 0
            set tomorrow to today + (1 * days)
            set out to ""
            repeat with c in calendars
                repeat with e in (every event of c whose start date ≥ today and start date < tomorrow)
                    set out to out & (summary of e) & "|" & ((start date of e) as string) & linefeed
                end repeat
            end repeat
            return out
        end tell
        '''
        ok, out = await _run(script, timeout=20.0)
        return ok, out, {}

    # ---------- Finder / Safari / Notes / Reminders ----------

    async def _finder_reveal(self, p: dict) -> tuple[bool, Any, dict]:
        path = p.get("path", "")
        script = f'tell application "Finder" to reveal POSIX file {_q(path)}\nactivate application "Finder"'
        ok, out = await _run(script)
        return ok, out, {"path": path}

    async def _finder_move_to_trash(self, p: dict) -> tuple[bool, Any, dict]:
        path = p.get("path", "")
        script = f'tell application "Finder" to delete POSIX file {_q(path)}'
        ok, out = await _run(script)
        return ok, out, {"path": path}

    async def _safari_open_url(self, p: dict) -> tuple[bool, Any, dict]:
        url = p.get("url", "")
        script = f'tell application "Safari"\nactivate\nmake new document with properties {{URL:{_q(url)}}}\nend tell'
        ok, out = await _run(script)
        return ok, out, {"url": url}

    async def _safari_current_url(self, p: dict) -> tuple[bool, Any, dict]:
        script = 'tell application "Safari" to return URL of current tab of front window'
        ok, out = await _run(script)
        return ok, out, {}

    async def _notes_create(self, p: dict) -> tuple[bool, Any, dict]:
        title = p.get("title", "")
        body = p.get("body", "")
        script = f'tell application "Notes" to make new note with properties {{name:{_q(title)}, body:{_q(body)}}}'
        ok, out = await _run(script)
        return ok, out, {"title": title}

    async def _reminders_create(self, p: dict) -> tuple[bool, Any, dict]:
        title = p.get("title", "")
        script = f'tell application "Reminders" to make new reminder with properties {{name:{_q(title)}}}'
        ok, out = await _run(script)
        return ok, out, {"title": title}

    async def _notification_show(self, p: dict) -> tuple[bool, Any, dict]:
        title = p.get("title", "ATLAS")
        message = p.get("message", "")
        script = f'display notification {_q(message)} with title {_q(title)}'
        ok, out = await _run(script)
        return ok, out, {}
