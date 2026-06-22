"""Internal Economy — per-call cost tracking.

Kee mostly runs on Coco's local hardware (free), but `claude_code` calls
the Sonnet/Opus tier through Coco's Pro/Max subscription which is
rate-limited and observable. Other paid endpoints may be added later
(Vercel functions, third-party APIs).

We log every call that returns a monetary or budgeted cost. Aggregations
(today, this week, by tool, by model) feed both the dashboard and a future
"budget remaining" alert.

Storage: SQLite `cost_ledger` table (`kee/core/db.py`). All access goes
through this module so the schema stays in one place.

Public surface:
  * `record(tool_name, cost_usd, ...)` — append a row
  * `from_claude_code_result(result, audit_id)` — extract cost from a
    `claude_code` tool result (the `raw_keys` includes `total_cost_usd`,
    `modelUsage`, `duration_api_ms`)
  * `summary(window=...)` — aggregate stats
  * `recent(n)` — last n rows
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from kee.core import db

logger = logging.getLogger(__name__)


@dataclass
class CostEntry:
    id: int
    timestamp: str
    tool_name: str
    cost_usd: float
    model: str | None
    duration_ms: int | None
    tokens_in: int | None
    tokens_out: int | None
    task_summary: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "timestamp": self.timestamp, "tool_name": self.tool_name,
            "cost_usd": round(self.cost_usd, 4), "model": self.model,
            "duration_ms": self.duration_ms, "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out, "task_summary": self.task_summary,
        }


def record(
    tool_name: str,
    cost_usd: float,
    model: str | None = None,
    duration_ms: int | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    task_summary: str | None = None,
    audit_id: int | None = None,
) -> int:
    """Append a cost row. Returns the row id."""
    if cost_usd < 0:
        cost_usd = 0.0
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cost_ledger
                (tool_name, cost_usd, model, duration_ms, tokens_in,
                 tokens_out, task_summary, audit_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tool_name, float(cost_usd), model, duration_ms, tokens_in,
             tokens_out, task_summary, audit_id),
        )
        return cur.lastrowid


def from_claude_code_result(
    result: dict[str, Any],
    task_summary: str | None = None,
    audit_id: int | None = None,
) -> int | None:
    """Best-effort extract from a claude_code tool result.

    Claude Code's JSON output includes `total_cost_usd`, `modelUsage`
    (per-model breakdown), `duration_api_ms`, and `usage` (overall token
    counts). Schema can drift — we tolerate missing fields.

    Returns the cost_ledger row id, or None if the result has no cost.
    """
    if not isinstance(result, dict):
        return None
    raw_keys = set(result.get("raw_keys") or [])
    cost = result.get("total_cost_usd") or result.get("cost_usd")

    # If the tool wrapper didn't bubble it up, try parsing from `result` field.
    if cost is None and "result" in result and isinstance(result["result"], str):
        # Sometimes the cost is embedded in the JSON-as-string body. We
        # don't parse it here — claude_code already exposes raw_keys.
        pass

    if cost is None and not raw_keys:
        return None

    cost_usd = float(cost or 0.0)
    if cost_usd <= 0 and not raw_keys:
        return None

    duration = result.get("duration_api_ms") or result.get("duration_ms")
    duration_ms = int(duration) if duration else None

    usage = result.get("usage") or {}
    tokens_in = usage.get("input_tokens") if isinstance(usage, dict) else None
    tokens_out = usage.get("output_tokens") if isinstance(usage, dict) else None

    model_usage = result.get("modelUsage") or {}
    model = None
    if isinstance(model_usage, dict) and model_usage:
        # Pick the model with the largest token usage (typically the only one).
        try:
            model = max(model_usage.items(),
                        key=lambda kv: (kv[1] or {}).get("inputTokens", 0)
                                      + (kv[1] or {}).get("outputTokens", 0))[0]
        except Exception:
            model = next(iter(model_usage.keys()))

    return record(
        tool_name="claude_code",
        cost_usd=cost_usd,
        model=model,
        duration_ms=duration_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        task_summary=task_summary,
        audit_id=audit_id,
    )


def summary(window_days: int | None = None) -> dict[str, Any]:
    """Aggregate stats. `window_days=None` means lifetime."""
    where = ""
    args: tuple = ()
    if window_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=window_days)
        where = " WHERE timestamp >= ?"
        args = (cutoff,)

    with db.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS total FROM cost_ledger{where}", args)
        row = cur.fetchone()
        total = float(row["total"])
        n = int(row["n"])

        cur.execute(
            f"""
            SELECT tool_name, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS sub
            FROM cost_ledger{where}
            GROUP BY tool_name
            ORDER BY sub DESC
            """,
            args,
        )
        by_tool = [{"tool": r["tool_name"], "calls": r["n"],
                    "spent_usd": round(float(r["sub"]), 4)} for r in cur.fetchall()]

        cur.execute(
            f"""
            SELECT model, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS sub
            FROM cost_ledger{where}
            WHERE model IS NOT NULL{(' AND timestamp >= ?' if where else '')}
            GROUP BY model
            ORDER BY sub DESC
            """,
            args if where else (),
        )
        by_model = [{"model": r["model"], "calls": r["n"],
                     "spent_usd": round(float(r["sub"]), 4)} for r in cur.fetchall()]

    return {
        "window_days": window_days,
        "total_calls": n,
        "total_spent_usd": round(total, 4),
        "by_tool": by_tool,
        "by_model": by_model,
    }


def recent(n: int = 20) -> list[CostEntry]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM cost_ledger ORDER BY id DESC LIMIT ?", (n,))
        rows = cur.fetchall()
    return [
        CostEntry(
            id=r["id"], timestamp=str(r["timestamp"]),
            tool_name=r["tool_name"], cost_usd=float(r["cost_usd"]),
            model=r["model"], duration_ms=r["duration_ms"],
            tokens_in=r["tokens_in"], tokens_out=r["tokens_out"],
            task_summary=r["task_summary"],
        )
        for r in rows
    ]
