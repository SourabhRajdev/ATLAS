"""Mail source — unread count + VIP senders via osascript.

Emits:
  - new_mail_from_vip (payload: sender, subject)
  - unread_burst      (> N new messages in last 5 min)
"""

from __future__ import annotations

import asyncio
import logging
import time

from atlas.autonomy.models import Signal

logger = logging.getLogger("atlas.autonomy.mail")

_VIP_SCRIPT = '''
tell application "Mail"
    set out to ""
    try
        set vips to messages of mailbox "VIPs" of account "VIPs"
    on error
        set vips to {}
    end try
    repeat with m in vips
        if read status of m is false then
            set out to out & (sender of m) & "§" & (subject of m) & linefeed
        end if
    end repeat
    return out
end tell
'''

_UNREAD_SCRIPT = '''
tell application "Mail"
    set n to 0
    repeat with acct in accounts
        repeat with box in mailboxes of acct
            try
                set n to n + (unread count of box)
            end try
        end repeat
    end repeat
    return n as string
end tell
'''


class MailSource:
    source = "mail"

    def __init__(self) -> None:
        self._seen_vip: set[str] = set()
        self._last_unread: int = -1
        self._last_burst_at: float = 0.0

    async def poll(self) -> list[Signal]:
        signals: list[Signal] = []
        vip_raw = await _run(_VIP_SCRIPT)
        for line in (vip_raw or "").splitlines():
            if "§" not in line:
                continue
            sender, subject = line.split("§", 1)
            key = f"{sender}:{subject}"
            if key in self._seen_vip:
                continue
            self._seen_vip.add(key)
            signals.append(Signal(
                source=self.source, kind="new_mail_from_vip",
                payload={"sender": sender.strip(), "subject": subject.strip()},
            ))

        unread_raw = await _run(_UNREAD_SCRIPT)
        try:
            unread = int((unread_raw or "0").strip())
        except ValueError:
            unread = 0
        if self._last_unread >= 0 and unread - self._last_unread >= 5:
            now = time.time()
            if now - self._last_burst_at > 600:
                self._last_burst_at = now
                signals.append(Signal(
                    source=self.source, kind="unread_burst",
                    payload={"delta": unread - self._last_unread, "total": unread},
                ))
        self._last_unread = unread
        return signals


async def _run(script: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return None
        return stdout.decode("utf-8", "replace")
    except Exception as e:
        logger.debug("mail run failed: %s", e)
        return None
