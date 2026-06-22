"""Gmail tool — read-only inbox triage (search + read threads).

We deliberately don't ship a `send` action here — the v2 spec puts email
SEND under risk 3 (external visible) and Resend (kee/tools/email_send.py)
is a cleaner outbound path that doesn't need OAuth or rate limits. Gmail
is for READING what landed in Coco's inbox.

Required scope: `https://www.googleapis.com/auth/gmail.readonly`.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from kee.distributed.google_oauth import get_credentials
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_SCOPES_RO = ["https://www.googleapis.com/auth/gmail.readonly"]


def _service():
    from googleapiclient.discovery import build
    creds = get_credentials(_SCOPES_RO, interactive=False)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
    except Exception:
        return ""


def _extract_text(payload: dict) -> str:
    """Walk a Gmail message payload and return the first text/plain body."""
    if not payload:
        return ""
    if payload.get("mimeType", "").startswith("text/plain"):
        body = payload.get("body", {}).get("data")
        if body:
            return _decode(body)
    for part in payload.get("parts", []) or []:
        text = _extract_text(part)
        if text:
            return text
    # Fall back to text/html (stripped of tags) if no plain part.
    if payload.get("mimeType", "").startswith("text/html"):
        body = payload.get("body", {}).get("data")
        if body:
            import re
            return re.sub(r"<[^>]+>", "", _decode(body))
    return ""


class GmailTool(Tool):
    name = "gmail"
    description = (
        "Read Gmail inbox. Read-only — sending email goes through "
        "`email_send` (Resend). Actions:\n"
        "  - 'search': search threads by `query` (Gmail search syntax). "
        "Returns thread IDs + snippets.\n"
        "  - 'read_thread': fetch full content of a `thread_id`.\n"
        "  - 'list_labels': show inbox labels (Inbox, Sent, custom labels).\n"
        "  - 'unread_count': how many unread in INBOX right now."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "read_thread", "list_labels", "unread_count"],
                "default": "unread_count",
            },
            "query": {
                "type": "string",
                "description": "Gmail search syntax (e.g. 'from:marco is:unread').",
            },
            "thread_id": {"type": "string"},
            "max_results": {"type": "integer", "default": 10},
        },
    }

    async def execute(
        self,
        action: str = "unread_count",
        query: str | None = None,
        thread_id: str | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        try:
            svc = _service()
        except RuntimeError as e:
            return {"status": "auth_required", "error": str(e)}

        try:
            if action == "list_labels":
                resp = svc.users().labels().list(userId="me").execute()
                return {
                    "status": "ok",
                    "labels": [
                        {"id": l["id"], "name": l["name"], "type": l.get("type")}
                        for l in resp.get("labels", [])
                    ],
                }

            if action == "unread_count":
                resp = svc.users().labels().get(userId="me", id="INBOX").execute()
                return {
                    "status": "ok",
                    "unread": resp.get("messagesUnread", 0),
                    "total": resp.get("messagesTotal", 0),
                }

            if action == "search":
                if not query:
                    return {"error": "search requires `query`"}
                resp = svc.users().threads().list(
                    userId="me", q=query, maxResults=max_results,
                ).execute()
                threads = resp.get("threads", [])
                # Hydrate each thread with the snippet + a few headers
                hydrated = []
                for t in threads[:max_results]:
                    detail = svc.users().threads().get(
                        userId="me", id=t["id"], format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    ).execute()
                    msgs = detail.get("messages", [])
                    last = msgs[-1] if msgs else {}
                    headers = {
                        h["name"]: h["value"]
                        for h in last.get("payload", {}).get("headers", [])
                    }
                    hydrated.append({
                        "thread_id": t["id"],
                        "snippet": detail.get("snippet", "")[:240],
                        "from": headers.get("From"),
                        "subject": headers.get("Subject"),
                        "date": headers.get("Date"),
                        "msg_count": len(msgs),
                    })
                return {"status": "ok", "count": len(hydrated), "threads": hydrated}

            if action == "read_thread":
                if not thread_id:
                    return {"error": "read_thread requires `thread_id`"}
                detail = svc.users().threads().get(
                    userId="me", id=thread_id, format="full",
                ).execute()
                msgs = []
                for m in detail.get("messages", []):
                    headers = {
                        h["name"]: h["value"]
                        for h in m.get("payload", {}).get("headers", [])
                    }
                    msgs.append({
                        "from": headers.get("From"),
                        "to": headers.get("To"),
                        "subject": headers.get("Subject"),
                        "date": headers.get("Date"),
                        "text": _extract_text(m.get("payload", {}))[:3000],
                    })
                return {"status": "ok", "thread_id": thread_id,
                        "snippet": detail.get("snippet", ""),
                        "messages": msgs}

            return {"error": f"unknown action {action!r}"}

        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}


tool = GmailTool()
