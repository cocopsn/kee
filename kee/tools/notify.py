"""Tool: notify — desktop toast notifications (Kee → Coco).

Used by the agent when it needs to surface something asynchronously
(e.g. heartbeat fired an actionable while the user wasn't typing,
Sleep Cycle finished its 4 AM pass and there's a digest to read,
Claude Code finished a long build). Not a substitute for inline
chat replies — only fire when the user might miss a chat-window
update.

Risk: 1 — externally visible (a toast pops in the OS), but
non-irreversible. Kee should default to `urgency='low'` unless
the user is genuinely time-pressed by the message.
"""

from __future__ import annotations

from typing import Any

from kee.perception.notifications import send_notification
from kee.tools.base import Tool


class NotifyTool(Tool):
    name = "notify"
    description = (
        "Send a desktop notification (Windows toast / Linux libnotify) to "
        "Armando. Use sparingly — only when (a) the user is likely AFK / "
        "in another app and (b) the information is time-sensitive enough "
        "that an inline chat reply could be missed. Examples: long-running "
        "Claude Code build finished, heartbeat detected disk low, Sleep "
        "Cycle proposal is ready for review. Default urgency is 'low'."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short header (≤40 chars)."},
            "message": {"type": "string", "description": "Body of the notification."},
            "urgency": {
                "type": "string",
                "enum": ["low", "normal", "critical"],
                "default": "low",
                "description": (
                    "Maps to platform semantics. 'critical' on Windows "
                    "plays a looping alarm — only use it when the user "
                    "must respond now (e.g. account locked, deploy failed)."
                ),
            },
            "duration_s": {"type": "integer", "default": 5},
        },
        "required": ["title", "message"],
    }

    async def execute(
        self,
        title: str,
        message: str,
        urgency: str = "low",
        duration_s: int = 5,
    ) -> dict[str, Any]:
        return send_notification(
            title=title[:80],
            message=message[:240],
            urgency=urgency,
            duration_s=duration_s,
        )


tool = NotifyTool()
