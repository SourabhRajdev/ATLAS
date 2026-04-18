"""Gmail integration — OAuth2 + History API polling.

Uses the Gmail History API to efficiently fetch only new messages since last
poll. Token refresh is handled automatically. Credentials stored in
data_dir/gmail_token.json.

Privacy: message bodies are summarized locally; full content only returned
when caller explicitly requests it. Subjects and sender info are fair game.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from atlas.integrations.base import BaseIntegration, IntegrationHealth

logger = logging.getLogger("atlas.integrations.gmail")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
]

_URGENT_KEYWORDS = frozenset({
    "urgent", "asap", "emergency", "critical", "immediately",
    "action required", "time sensitive",
})


def _is_urgent(subject: str, snippet: str) -> bool:
    text = (subject + " " + snippet).lower()
    return any(kw in text for kw in _URGENT_KEYWORDS)


def _decode_header_value(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


class GmailIntegration(BaseIntegration):
    name = "gmail"

    def __init__(self, data_dir: Path, credentials_file: Path | None = None) -> None:
        super().__init__()
        self._data_dir = data_dir
        self._credentials_file = credentials_file
        self._token_path = data_dir / "gmail_token.json"
        self._history_path = data_dir / "gmail_history.json"
        self._service: Any = None
        self._history_id: str | None = self._load_history_id()

    def _load_history_id(self) -> str | None:
        if self._history_path.exists():
            try:
                return json.loads(self._history_path.read_text()).get("history_id")
            except Exception:
                pass
        return None

    def _save_history_id(self, history_id: str) -> None:
        self._history_path.write_text(json.dumps({"history_id": history_id}))
        self._history_id = history_id

    def _get_service(self) -> Any:
        """Return authenticated Gmail API service, building it if needed."""
        if self._service is not None:
            return self._service

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            from google.auth.transport.requests import Request
        except ImportError:
            raise RuntimeError(
                "Gmail integration requires: pip install google-auth-oauthlib google-api-python-client"
            )

        creds = None
        if self._token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif self._credentials_file and self._credentials_file.exists():
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self._credentials_file), SCOPES
                )
                creds = flow.run_local_server(port=0)
            else:
                raise RuntimeError(
                    f"Gmail credentials not found. Run OAuth flow with credentials at "
                    f"{self._credentials_file or '<not set>'}"
                )
            self._token_path.write_text(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def _fetch_message_meta(self, service: Any, msg_id: str) -> dict | None:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["Subject", "From", "Date", "To"],
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            subject = _decode_header_value(headers, "Subject")
            sender = _decode_header_value(headers, "From")
            date_str = _decode_header_value(headers, "Date")
            snippet = msg.get("snippet", "")
            labels = msg.get("labelIds", [])
            return {
                "id": msg_id,
                "thread_id": msg.get("threadId", ""),
                "subject": subject,
                "from": sender,
                "date": date_str,
                "snippet": snippet,
                "labels": labels,
                "is_urgent": _is_urgent(subject, snippet),
                "is_unread": "UNREAD" in labels,
            }
        except Exception as e:
            logger.warning("Failed to fetch message %s: %s", msg_id, e)
            return None

    async def poll(self) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._poll_sync)

    def _poll_sync(self) -> list[dict]:
        events: list[dict] = []
        try:
            service = self._get_service()
        except Exception as e:
            self._fail(str(e))
            return []

        try:
            if self._history_id is None:
                # First run: get current history ID and recent messages
                profile = service.users().getProfile(userId="me").execute()
                self._save_history_id(str(profile["historyId"]))
                # Fetch last 20 unread messages as initial state
                result = service.users().messages().list(
                    userId="me", labelIds=["UNREAD", "INBOX"], maxResults=20
                ).execute()
                messages = result.get("messages", [])
                for m in messages:
                    meta = self._fetch_message_meta(service, m["id"])
                    if meta:
                        events.append({"type": "email_received", "source": "gmail", **meta})
            else:
                # Incremental: use History API
                try:
                    history_result = service.users().history().list(
                        userId="me",
                        startHistoryId=self._history_id,
                        historyTypes=["messageAdded"],
                        labelId="INBOX",
                    ).execute()
                except Exception as e:
                    if "startHistoryId" in str(e):
                        # History ID expired — reset
                        self._history_id = None
                        self._save_history_id("0")
                        self._ok()
                        return []
                    raise

                history = history_result.get("history", [])
                new_history_id = history_result.get("historyId", self._history_id)

                seen_ids: set[str] = set()
                for record in history:
                    for added in record.get("messagesAdded", []):
                        msg_id = added["message"]["id"]
                        if msg_id not in seen_ids:
                            seen_ids.add(msg_id)
                            meta = self._fetch_message_meta(service, msg_id)
                            if meta:
                                events.append({"type": "email_received", "source": "gmail", **meta})

                self._save_history_id(str(new_history_id))

            self._ok({"events_this_poll": len(events)})
        except Exception as e:
            logger.error("Gmail poll error: %s", e)
            self._fail(str(e))

        return events

    def health_check(self) -> IntegrationHealth:
        return self._health
