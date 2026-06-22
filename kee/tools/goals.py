"""Goals tool — typed access to vault/config/goals.md.

The agent should reach for this instead of `files action=read path=…goals.md`
followed by ad-hoc parsing. It returns structured records and supports a few
common queries: list active, list upcoming-in-N-days, list overdue.

Risk: 0 (read-only).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from kee.perception.goals import load_goals
from kee.tools.base import Tool


def _serialize(g) -> dict[str, Any]:
    return {
        "title": g.title,
        "status": g.status,
        "deadline": g.deadline.isoformat() if g.deadline else None,
        "days_left": g.days_to_deadline(),
        "project": g.project,
        "progress_pct": g.progress_pct,
        "notes": g.notes,
        "extras": g.extras,
    }


class GoalsTool(Tool):
    name = "goals"
    description = (
        "Query Armando's active goals from the vault (vault/config/goals.md). "
        "Use this for ANY question about pending work, deadlines, what's due "
        "this week, status of a project, etc. Do NOT read goals.md with the "
        "files tool — use this instead so you get structured records.\n"
        "Actions:\n"
        "  - 'all'      → every goal in the file (any status)\n"
        "  - 'active'   → only goals with status active/in_progress (default)\n"
        "  - 'upcoming' → active goals with deadline within `horizon_days` "
        "(default 7)\n"
        "  - 'overdue'  → active goals whose deadline has passed"
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["all", "active", "upcoming", "overdue"],
                "default": "active",
            },
            "horizon_days": {
                "type": "integer",
                "description": "Look-ahead window for action='upcoming'. Default 7.",
                "default": 7,
            },
        },
    }

    async def execute(
        self,
        action: str = "active",
        horizon_days: int = 7,
    ) -> dict[str, Any]:
        all_goals = load_goals()
        today = date.today()

        if action == "all":
            picked = all_goals
        elif action == "active":
            picked = [g for g in all_goals if g.is_active()]
        elif action == "upcoming":
            horizon = today + timedelta(days=horizon_days)
            picked = [
                g for g in all_goals
                if g.is_active() and g.deadline and today <= g.deadline <= horizon
            ]
        elif action == "overdue":
            picked = [
                g for g in all_goals
                if g.is_active() and g.deadline and g.deadline < today
            ]
        else:
            return {"error": f"unknown action: {action}"}

        return {
            "action": action,
            "count": len(picked),
            "goals": [_serialize(g) for g in picked],
            "today": today.isoformat(),
        }


tool = GoalsTool()
