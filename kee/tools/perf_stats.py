"""Tool: perf_stats — pipeline introspection over audit_log.

Surfaces the data the dashboard already has on a tool-callable interface
so the agent can reason about its own performance ("the LLM has been
slow today; consider switching primary").

Five views:
  - 'overview':    one-liner per layer (LLM, tools, surfaces) with averages
  - 'llm':         per-provider call counts, avg latency, cost
  - 'tools':       per-tool counts + success rate + avg latency
  - 'surfaces':    per-source request volume (chat, voice, telegram, edge)
  - 'slow':        slowest N calls in window
  - 'cost':        cost breakdown by provider + tier

Risk: 0 (read-only over audit_log).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

from kee.core import db
from kee.tools.base import Tool


def _scope(window_hours: int) -> str:
    return f"timestamp >= datetime('now', '-{int(window_hours)} hours')"


def _overview(window_hours: int) -> dict:
    conn = db.get_connection(); cur = conn.cursor()
    out: dict[str, Any] = {"window_hours": window_hours}

    # LLM totals
    try:
        cur.execute(f"SELECT COUNT(*), SUM(cost_usd), AVG(latency_ms) FROM audit_log "
                    f"WHERE provider IS NOT NULL AND {_scope(window_hours)}")
        n, cost, lat = cur.fetchone()
        out["llm_calls"] = n or 0
        out["llm_total_cost_usd"] = round(cost or 0, 4)
        out["llm_avg_latency_ms"] = int(lat) if lat else None
    except Exception:
        pass

    # Tool totals
    try:
        cur.execute(f"SELECT COUNT(*), SUM(success), AVG(latency_ms) FROM audit_log "
                    f"WHERE tool_name IS NOT NULL AND {_scope(window_hours)}")
        n, ok, lat = cur.fetchone()
        out["tool_calls"] = n or 0
        out["tool_success_rate"] = round((ok / n) if n else 0, 3)
        out["tool_avg_latency_ms"] = int(lat) if lat else None
    except Exception:
        pass

    # Anomalies
    try:
        cur.execute(f"SELECT COUNT(*) FROM anomalies WHERE {_scope(window_hours)}")
        out["anomalies"] = cur.fetchone()[0] or 0
    except Exception:
        out["anomalies"] = 0

    return out


def _by_llm(window_hours: int) -> dict:
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute(f"""
        SELECT provider, COUNT(*) as n, SUM(cost_usd) as cost,
               AVG(latency_ms) as lat, SUM(tokens_in) as ti, SUM(tokens_out) as to_
        FROM audit_log WHERE provider IS NOT NULL AND {_scope(window_hours)}
        GROUP BY provider ORDER BY n DESC
    """)
    rows = []
    for prov, n, cost, lat, ti, to in cur.fetchall():
        rows.append({
            "provider": prov, "calls": n,
            "cost_usd": round(cost or 0, 4),
            "avg_latency_ms": int(lat) if lat else None,
            "tokens_in": int(ti or 0),
            "tokens_out": int(to or 0),
        })
    return {"window_hours": window_hours, "providers": rows}


def _by_tool(window_hours: int) -> dict:
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute(f"""
        SELECT tool_name, COUNT(*), SUM(success), AVG(latency_ms)
        FROM audit_log WHERE tool_name IS NOT NULL AND {_scope(window_hours)}
        GROUP BY tool_name ORDER BY COUNT(*) DESC
    """)
    rows = []
    for name, n, ok, lat in cur.fetchall():
        rows.append({
            "tool": name, "calls": n,
            "success_rate": round((ok / n) if n else 0, 3),
            "avg_latency_ms": int(lat) if lat else None,
        })
    return {"window_hours": window_hours, "tools": rows}


def _by_surface(window_hours: int) -> dict:
    """Approximate via conversations.source if available, else messages."""
    conn = db.get_connection(); cur = conn.cursor()
    rows: list[dict] = []
    # Try to use messages table if it carries source
    try:
        cur.execute(f"SELECT source, COUNT(*) FROM messages WHERE {_scope(window_hours)} "
                    f"GROUP BY source")
        for src, n in cur.fetchall():
            rows.append({"source": src or "?", "messages": n})
    except Exception:
        pass
    return {"window_hours": window_hours, "by_source": rows}


def _slowest(window_hours: int, n: int = 10) -> dict:
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute(f"""
        SELECT timestamp, tool_name, provider, model_name, latency_ms
        FROM audit_log
        WHERE latency_ms IS NOT NULL AND {_scope(window_hours)}
        ORDER BY latency_ms DESC LIMIT ?
    """, (n,))
    rows = []
    for ts, tn, pv, md, lat in cur.fetchall():
        rows.append({"timestamp": ts, "tool": tn, "provider": pv,
                     "model": md, "latency_ms": int(lat)})
    return {"window_hours": window_hours, "slowest": rows}


def _cost_breakdown(window_hours: int) -> dict:
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute(f"""
        SELECT provider, tier, model_name, COUNT(*), SUM(cost_usd)
        FROM audit_log
        WHERE provider IS NOT NULL AND {_scope(window_hours)}
        GROUP BY provider, tier, model_name
        ORDER BY SUM(cost_usd) DESC
    """)
    rows = []
    total = 0.0
    for prov, tier, mod, n, cost in cur.fetchall():
        cost = cost or 0
        total += cost
        rows.append({"provider": prov, "tier": tier, "model": mod,
                     "calls": n, "cost_usd": round(cost, 4)})
    return {"window_hours": window_hours, "rows": rows,
            "total_cost_usd": round(total, 4)}


class PerfStatsTool(Tool):
    name = "perf_stats"
    description = (
        "Pipeline introspection over audit_log. Use to debug latency, "
        "cost, success rates by layer (LLM / tool / surface). "
        "Same data the dashboard renders, exposed as a tool so the agent "
        "can reason about its own performance.\n"
        "Actions: 'overview' | 'llm' | 'tools' | 'surfaces' | 'slow' | 'cost'"
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "view": {
                "type": "string",
                "enum": ["overview", "llm", "tools", "surfaces", "slow", "cost"],
                "default": "overview",
            },
            "window_hours": {"type": "integer", "default": 24},
            "n": {"type": "integer", "default": 10,
                  "description": "For 'slow': how many slowest entries"},
        },
    }

    async def execute(
        self, view: str = "overview",
        window_hours: int = 24, n: int = 10,
    ) -> dict[str, Any]:
        if view == "overview": return _overview(window_hours)
        if view == "llm":      return _by_llm(window_hours)
        if view == "tools":    return _by_tool(window_hours)
        if view == "surfaces": return _by_surface(window_hours)
        if view == "slow":     return _slowest(window_hours, n)
        if view == "cost":     return _cost_breakdown(window_hours)
        return {"ok": False, "error": f"unknown view '{view}'"}


tool = PerfStatsTool()
