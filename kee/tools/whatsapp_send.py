"""WhatsApp send tool — outbound via Meta Cloud API.

Uses Coco's AUCTORUM-issued credentials — same `WHATSAPP_TOKEN` and
`WHATSAPP_PHONE_NUMBER_ID` that drive the AUCTORUM agents. Add them
to `D:/Kee/.env` (the import_keys helper makes this 1 click) to
enable.

Risk: 3 — outbound external messaging. Same posture as `email_send`:
only ever fire when Coco said "send WhatsApp to X" in the same turn,
never on inferred intent.

Receive-side (inbound webhook → agent) is a separate piece in
`kee/surfaces/whatsapp.py` (Phase 6, needs a public URL).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_GRAPH_URL = "https://graph.facebook.com/v21.0/{phone_id}/messages"


def _e164(number: str) -> str:
    """Normalise a number to E.164-ish: strip spaces, dashes; keep leading +."""
    n = re.sub(r"[^\d+]", "", number.strip())
    if n.startswith("00"):
        n = "+" + n[2:]
    return n


class WhatsAppSendTool(Tool):
    name = "whatsapp_send"
    description = (
        "Send a WhatsApp text or template message via Meta Cloud API. "
        "**Risk 3 — externally visible.** Use only when Coco said 'manda "
        "WhatsApp a X' or similar IN THIS TURN. Never auto-send.\n"
        "Two modes:\n"
        "  - `text` (default): freeform body. Note: Meta only allows "
        "freeform if the conversation was opened by the recipient in the "
        "last 24 h. Otherwise you must use a template.\n"
        "  - `template`: pre-approved Meta template by `template_name` + "
        "optional `template_params`. Always allowed.\n"
        "Returns Meta's message id and the recipient's wa_id."
    )
    risk_level = 3
    parameters_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "E.164 number (e.g. +5218441234567) or any format we'll normalise."},
            "text": {"type": "string"},
            "template_name": {"type": "string"},
            "template_params": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Body parameters for the template, in order.",
            },
            "template_language": {"type": "string", "default": "es_MX"},
        },
        "required": ["to"],
    }

    async def execute(
        self,
        to: str,
        text: str | None = None,
        template_name: str | None = None,
        template_params: list[str] | None = None,
        template_language: str = "es_MX",
    ) -> dict[str, Any]:
        token = os.environ.get("WHATSAPP_TOKEN", "").strip()
        phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        if not (token and phone_id):
            return {
                "status": "missing_dependency",
                "error": (
                    "WHATSAPP_TOKEN + WHATSAPP_PHONE_NUMBER_ID must be in env. "
                    "Run `python scripts/import_keys.py` to copy from your "
                    "auctorum-systems .env.local."
                ),
            }
        if not (text or template_name):
            return {"error": "Need `text` OR `template_name`."}

        recipient = _e164(to)
        if recipient.startswith("+"):
            recipient_wa = recipient[1:]
        else:
            recipient_wa = recipient

        body: dict[str, Any]
        if template_name:
            components = []
            if template_params:
                components = [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(p)} for p in template_params],
                }]
            body = {
                "messaging_product": "whatsapp",
                "to": recipient_wa,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": template_language},
                    "components": components,
                },
            }
        else:
            body = {
                "messaging_product": "whatsapp",
                "to": recipient_wa,
                "type": "text",
                "text": {"body": text},
            }

        url = _GRAPH_URL.format(phone_id=phone_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                r = await client.post(
                    url, json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
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
            msg = (data.get("messages") or [{}])[0]
            wa = (data.get("contacts") or [{}])[0]
            return {
                "status": "sent",
                "message_id": msg.get("id"),
                "wa_id": wa.get("wa_id"),
                "to": recipient,
                "mode": "template" if template_name else "text",
            }
        return {
            "status": "failed",
            "http_status": r.status_code,
            "error": data,
        }


tool = WhatsAppSendTool()
