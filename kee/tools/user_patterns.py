"""Tool: user_patterns — query Sleep Cycle's behavioural model.

Surfaces the data Sleep Cycle accumulates in
`vault/config/user_behavior.json` (axioms + stats per day) plus derived
patterns from `audit_log` (peak activity hours, most-used tools, common
sources, daily cost trend).

Useful so:
  - the agent can self-customize ("Coco usually works on Auctorum at
    night → don't ping him about goals before noon")
  - the dashboard can render a "Coco's habits" page
  - other tools (heartbeat, proactive, planner) can lean on it

Risk: 0 (read-only over our own behavioral model).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from kee.config import settings
from kee.core import db
from kee.tools.base import Tool


_BEHAVIOR_PATH = settings.vault_dir / "config" / "user_behavior.json"


def _load_behavior() -> dict:
    if not _BEHAVIOR_PATH.exists():
        return {}
    try:
        return json.loads(_BEHAVIOR_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _peak_hours(window_days: int = 14) -> dict:
    """Hour-of-day distribution of audit_log entries."""
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute(f"""
        SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hr, COUNT(*)
        FROM audit_log
        WHERE timestamp >= datetime('now', '-{int(window_days)} days')
        GROUP BY hr ORDER BY hr
    """)
    by_hour = {h: 0 for h in range(24)}
    for h, n in cur.fetchall():
        by_hour[h] = n
    if not any(by_hour.values()):
        return {"window_days": window_days, "no_data": True}
    peak = max(by_hour, key=lambda h: by_hour[h])
    avg = sum(by_hour.values()) / 24
    return {
        "window_days": window_days,
        "peak_hour": peak,
        "peak_count": by_hour[peak],
        "avg_per_hour": round(avg, 1),
        "hours": [{"hour": h, "n": by_hour[h]} for h in range(24)],
    }


def _tool_usage(window_days: int = 14, top: int = 10) -> dict:
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute(f"""
        SELECT tool_name, COUNT(*), SUM(success)
        FROM audit_log
        WHERE tool_name IS NOT NULL
          AND timestamp >= datetime('now', '-{int(window_days)} days')
        GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT ?
    """, (top,))
    return {
        "window_days": window_days,
        "top_tools": [
            {"tool": r[0], "calls": r[1],
             "success_rate": round((r[2] or 0) / r[1], 2)}
            for r in cur.fetchall()
        ],
    }


def _surface_distribution(window_days: int = 14) -> dict:
    conn = db.get_connection(); cur = conn.cursor()
    rows = []
    try:
        cur.execute(f"""
            SELECT source, COUNT(*) FROM messages
            WHERE timestamp >= datetime('now', '-{int(window_days)} days')
            GROUP BY source ORDER BY COUNT(*) DESC
        """)
        for src, n in cur.fetchall():
            rows.append({"source": src or "?", "messages": n})
    except Exception:
        pass
    return {"window_days": window_days, "by_source": rows}


def _cost_trend(window_days: int = 14) -> dict:
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute(f"""
        SELECT date(timestamp) as d, SUM(cost_usd) as cost, COUNT(*) as calls
        FROM audit_log
        WHERE provider IS NOT NULL
          AND timestamp >= datetime('now', '-{int(window_days)} days')
        GROUP BY d ORDER BY d
    """)
    days = [{"date": r[0], "cost_usd": round(r[1] or 0, 4), "calls": r[2]}
            for r in cur.fetchall()]
    return {"window_days": window_days, "days": days,
            "total_cost_usd": round(sum(d["cost_usd"] for d in days), 4)}


def _axioms() -> dict:
    """Recent axioms from Sleep Cycle."""
    bh = _load_behavior()
    return {
        "history_days": len(bh.get("history", [])),
        "current_axioms": bh.get("axioms", []),
        "history_excerpt": bh.get("history", [])[-3:] if bh.get("history") else [],
    }


def _derived_summary(window_days: int = 14) -> dict:
    """One-liner per category for quick agent context."""
    peak = _peak_hours(window_days)
    tools = _tool_usage(window_days, top=5)
    cost = _cost_trend(window_days)
    axi = _axioms()
    summary = []
    if not peak.get("no_data"):
        summary.append(
            f"Peak activity hour: {peak['peak_hour']:02d}:00 "
            f"({peak['peak_count']} actions/h vs {peak['avg_per_hour']:.0f} avg)."
        )
    if tools.get("top_tools"):
        names = ", ".join(t["tool"] for t in tools["top_tools"][:3])
        summary.append(f"Top 3 tools last {window_days}d: {names}.")
    if cost.get("days"):
        summary.append(f"Total LLM spend last {window_days}d: ${cost['total_cost_usd']:.2f}.")
    if axi.get("current_axioms"):
        summary.append(f"{len(axi['current_axioms'])} active behavioural axioms.")
    return {
        "window_days": window_days,
        "summary": " ".join(summary) if summary else "No data yet.",
        "peak": peak, "tools": tools, "cost": cost, "axioms": axi,
    }


class UserPatternsTool(Tool):
    name = "user_patterns"
    description = (
        "Behavioural intelligence about Coco — peak activity hours, "
        "most-used tools, surface distribution (chat/voice/telegram), "
        "daily cost trend, and Sleep Cycle's accumulated axioms. "
        "Use this BEFORE deciding to interrupt the user or to pick a "
        "default tool — it grounds proactive suggestions in real "
        "behaviour rather than guesses.\n"
        "Views: 'summary' | 'peak' | 'tools' | 'surfaces' | 'cost' | 'axioms'"
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "view": {
                "type": "string",
                "enum": ["summary", "peak", "tools", "surfaces", "cost", "axioms"],
                "default": "summary",
            },
            "window_days": {"type": "integer", "default": 14},
        },
    }

    async def execute(
        self, view: str = "summary", window_days: int = 14,
    ) -> dict[str, Any]:
        if view == "summary":  return _derived_summary(window_days)
        if view == "peak":     return _peak_hours(window_days)
        if view == "tools":    return _tool_usage(window_days)
        if view == "surfaces": return _surface_distribution(window_days)
        if view == "cost":     return _cost_trend(window_days)
        if view == "axioms":   return _axioms()
        return {"ok": False, "error": f"unknown view '{view}'"}


tool = UserPatternsTool()
