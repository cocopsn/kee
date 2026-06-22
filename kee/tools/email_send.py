"""Email-send tool — uses Resend (https://resend.com) to ship outbound mail.

Resend is simpler than Gmail-send: a single API key, no OAuth, no
delegation. Coco already has `RESEND_API_KEY` configured for AUCTORUM —
add it to `D:/Kee/.env` (or export `RESEND_API_KEY` in the shell) to
enable this tool.

Risk: 3 — outbound email is externally visible AND irreversible. The
agent should ALWAYS confirm with the user before sending unless the
user explicitly said "send X to Y" in the same turn.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_RESEND_URL = "https://api.resend.com/emails"


class EmailSendTool(Tool):
    name = "email_send"
    description = (
        "Send an email via Resend. **Risk 3 — externally visible, "
        "irreversible.** Only call when Armando explicitly told you to "
        "send THIS specific email; never auto-send drafts. Body can be "
        "plain text (default) or HTML. Requires `RESEND_API_KEY` in env "
        "and a verified sending domain on Resend.\n"
        "Returns the Resend `id` on success — useful for tracking."
    )
    risk_level = 3
    parameters_schema = {
        "type": "object",
        "properties": {
            "to": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": "Recipient email or list of emails.",
            },
            "subject": {"type": "string"},
            "text": {"type": "string", "description": "Plain text body."},
            "html": {"type": "string", "description": "HTML body (overrides text)."},
            "from_": {
                "type": "string",
                "description": (
                    "Sender. Defaults to `RESEND_FROM` env var, or "
                    "'onboarding@resend.dev' (Resend's sandbox sender)."
                ),
            },
            "reply_to": {"type": "string"},
            "cc": {"type": "array", "items": {"type": "string"}},
            "bcc": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["to", "subject"],
    }

    async def execute(
        self,
        to: str | list[str],
        subject: str,
        text: str | None = None,
        html: str | None = None,
        from_: str | None = None,
        reply_to: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        api_key = os.environ.get("RESEND_API_KEY", "").strip()
        if not api_key:
            return {
                "status": "missing_dependency",
                "error": "RESEND_API_KEY not set in env. Add it to D:/Kee/.env.",
            }
        if not (text or html):
            return {"error": "Need `text` or `html` body."}

        sender = from_ or os.environ.get("RESEND_FROM") or "onboarding@resend.dev"
        recipients = [to] if isinstance(to, str) else list(to)

        body: dict[str, Any] = {
            "from": sender,
            "to": recipients,
            "subject": subject,
        }
        if html:
            body["html"] = html
        if text:
            body["text"] = text
        if reply_to:
            body["reply_to"] = reply_to
        if cc:
            body["cc"] = cc
        if bcc:
            body["bcc"] = bcc

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                r = await client.post(
                    _RESEND_URL, json=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
            except httpx.HTTPError as e:
                return {"status": "network_error", "error": str(e)}

        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:300]}

        if r.status_code == 200:
            return {
                "status": "sent",
                "id": data.get("id"),
                "to": recipients,
                "subject": subject,
            }
        return {
            "status": "failed",
            "http_status": r.status_code,
            "error": data,
        }


tool = EmailSendTool()
