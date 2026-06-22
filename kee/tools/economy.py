"""Tool: economy — query the Internal Economy ledger.

Phase 5 §III Gap 10. Reads only. Writes happen automatically inside the
tools that have a cost (right now: `claude_code`).

Risk: 0.
"""

from __future__ import annotations

from typing import Any

from kee.cognition import economy
from kee.tools.base import Tool


class EconomyTool(Tool):
    name = "economy"
    description = (
        "Query Kee's spend on paid tools. Right now `claude_code` calls "
        "are tracked (Coco's Pro/Max subscription is rate-limited so the "
        "raw cost lands here even though no per-call invoice is issued). "
        "Future paid integrations land in the same ledger.\n"
        "Actions:\n"
        "  - 'summary': aggregate (window_days optional; default lifetime)\n"
        "  - 'recent':  the last n entries (default 20)\n"
        "  - 'today':   shorthand for summary(window_days=1)\n"
        "  - 'week':    shorthand for summary(window_days=7)"
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["summary", "recent", "today", "week"],
                "default": "summary",
            },
            "window_days": {"type": "integer"},
            "n": {"type": "integer", "default": 20},
        },
    }

    async def execute(
        self,
        action: str = "summary",
        window_days: int | None = None,
        n: int = 20,
    ) -> dict[str, Any]:
        if action == "summary":
            return economy.summary(window_days=window_days)
        if action == "today":
            return economy.summary(window_days=1)
        if action == "week":
            return economy.summary(window_days=7)
        if action == "recent":
            entries = economy.recent(n=n)
            return {"count": len(entries), "entries": [e.to_dict() for e in entries]}
        return {"error": f"unknown action {action!r}"}


tool = EconomyTool()
