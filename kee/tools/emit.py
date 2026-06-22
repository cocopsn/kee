"""Tool: emit — push a notification to Coco through the smart router.

The agent calls this to deliver an out-of-band message (e.g. "I finished
the deploy, here's the URL") that should reach Coco even if he's away
from the surface. Routes through `notification_router.notify_smart` so
quiet hours / focus / urgency rules apply automatically.

Use sparingly — every emit cuts through a channel boundary and can be
disruptive. Prefer this over `voice` / TTS for things that should
persist (Telegram message stays visible after voice fades).

Risk: 2 — sends an outbound message to the user.
"""

from __future__ import annotations

import logging
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


class EmitTool(Tool):
    name = "emit"
    description = (
        "Empuja una notificación a Coco fuera de banda (Telegram + "
        "desktop, según el smart router). Úsalo cuando algo asíncrono "
        "termina y necesita llegar aunque Coco haya cambiado de surface "
        "(ej. 'deploy listo, URL: …'). NO uses para chit-chat, NO uses "
        "para confirmar acciones que ya respondiste por voz/chat. "
        "Respeta quiet hours y focus drift automáticamente.\n"
        "NOT accepted: query, message_to_user — usa `body` y `title`."
    )
    risk_level = 2
    parameters_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Header (≤80 chars)."},
            "body": {"type": "string", "description": "Cuerpo del mensaje."},
            "urgency": {
                "type": "string",
                "enum": ["low", "normal", "critical"],
                "default": "normal",
            },
            "kind": {
                "type": "string", "default": "agent_emit",
                "description": "Tag libre para audit/log filtering.",
            },
            "project_hint": {
                "type": "string",
                "description": "Si la notificación es sobre un proyecto, "
                               "inclúyelo aquí — el router lo cruza con "
                               "el focus session activo para decidir si "
                               "interrumpir el escritorio.",
            },
        },
        "required": ["title", "body"],
    }

    async def execute(
        self,
        title: str,
        body: str,
        urgency: str = "normal",
        kind: str = "agent_emit",
        project_hint: str | None = None,
    ) -> dict[str, Any]:
        if not title or not body:
            return {"ok": False, "error": "title + body required"}
        try:
            from kee.perception.notification_router import notify_smart
            res = await notify_smart(
                title=title[:80], body=body, urgency=urgency, kind=kind,
                project_hint=project_hint,
            )
            res["ok"] = True
            return res
        except Exception as e:
            logger.exception("emit failed")
            return {"ok": False, "error": str(e)}


tool = EmitTool()
