"""Tool: tool_reliability — query historical tool success rates.

Reads `audit_log` to compute per-tool success %, latency stats, and
the most common failure modes. Drives:
  - the dynamic autonomy threshold (already wired in `kee/cognition/autonomy.py`)
  - router decisions (Jarvis-pattern: low-reliability tools get blocked)
  - the dashboard Tools page (sort by reliability)

Risk: 0 (read-only over our own audit table).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from kee.core import db
from kee.tools.base import Tool


def _hallucinations(
    window_days: int, tool_name: Optional[str] = None,
) -> dict[str, Counter]:
    """Aggregate `kwarg_hallucination` rows: which kwargs the LLM keeps
    inventing for each tool. Sleep Cycle uses this to rewrite tool
    descriptions ("query is NOT a parameter").
    """
    conn = db.get_connection()
    cur = conn.cursor()
    where = ("action = 'kwarg_hallucination' "
             "AND timestamp >= datetime('now', ? || ' days')")
    params: list = [f"-{window_days}"]
    if tool_name:
        where += " AND tool_name = ?"
        params.append(tool_name)
    out: dict[str, Counter] = {}
    try:
        cur.execute(
            f"SELECT tool_name, parameters FROM audit_log WHERE {where}",
            params,
        )
        import json as _json
        for name, raw in cur.fetchall():
            if not raw or not name:
                continue
            try:
                payload = _json.loads(raw)
            except Exception:
                continue
            unknowns = payload.get("unknown") or []
            c = out.setdefault(name, Counter())
            for u in unknowns:
                c[u] += 1
    except Exception:
        # Table or column may not exist on very old DBs.
        pass
    return out


def _stats(window_days: int = 7, tool_name: Optional[str] = None) -> dict:
    conn = db.get_connection()
    cur = conn.cursor()
    where = ("timestamp >= datetime('now', ? || ' days') "
             "AND tool_name IS NOT NULL "
             "AND action != 'kwarg_hallucination'")
    params: list = [f"-{window_days}"]
    if tool_name:
        where += " AND tool_name = ?"
        params.append(tool_name)

    cur.execute(
        f"SELECT tool_name, success, error, latency_ms FROM audit_log WHERE {where}",
        params,
    )
    by_tool: dict[str, dict] = {}
    for name, ok, err, lat in cur.fetchall():
        d = by_tool.setdefault(name, {
            "calls": 0, "ok": 0, "fail": 0,
            "latency_ms_total": 0, "latency_n": 0,
            "error_types": Counter(),
        })
        d["calls"] += 1
        d["ok" if ok else "fail"] += 1
        if lat is not None:
            d["latency_ms_total"] += lat
            d["latency_n"] += 1
        if not ok and err:
            # Bucket by error class prefix
            cls = (err.split(":")[0] or "Unknown")[:40]
            d["error_types"][cls] += 1

    halluc = _hallucinations(window_days=window_days, tool_name=tool_name)
    # Wilson lower bound — penalises low-N tools so 1/1 isn't ranked above
    # 49/50. Same math the autonomy module uses to gate risky calls.
    from kee.cognition.autonomy import wilson_lower_bound as _wlb

    out = []
    for name, d in by_tool.items():
        rate = d["ok"] / d["calls"] if d["calls"] else 0.0
        avg_lat = (d["latency_ms_total"] / d["latency_n"]) if d["latency_n"] else None
        h = halluc.get(name, Counter())
        out.append({
            "tool": name,
            "calls": d["calls"],
            "ok": d["ok"],
            "fail": d["fail"],
            "success_rate": round(rate, 3),
            "trust_score": round(_wlb(d["ok"], d["calls"]), 3),
            "avg_latency_ms": int(avg_lat) if avg_lat else None,
            "top_errors": d["error_types"].most_common(3),
            "hallucinated_kwargs": h.most_common(5),
            "hallucination_rate": (
                round(sum(h.values()) / d["calls"], 3) if d["calls"] else 0.0
            ),
        })
    # Tools that were hallucinated-on but never invoked successfully (rare;
    # could happen if every call landed on a wrong tool name) are surfaced
    # too, with calls=0 so the dashboard can flag them separately.
    for name, h in halluc.items():
        if name in by_tool:
            continue
        out.append({
            "tool": name, "calls": 0, "ok": 0, "fail": 0,
            "success_rate": 0.0, "trust_score": 0.0,
            "avg_latency_ms": None,
            "top_errors": [], "hallucinated_kwargs": h.most_common(5),
            "hallucination_rate": None,
        })
    # Sort by trust_score desc (Wilson-corrected) so the dashboard shows
    # the most reliable tools first, not the noisy 1/1 = 100% ones.
    out.sort(key=lambda r: (r["trust_score"], r["calls"]), reverse=True)
    return {"window_days": window_days, "tools": out}


class ToolReliabilityTool(Tool):
    name = "tool_reliability"
    description = (
        "Query per-tool historical success rates from audit_log. Use to "
        "see which tools have been failing (e.g. 'files: 5/10 last week, "
        "mostly NotADirectoryError'), to debug, and to inform routing "
        "decisions (low-reliability tools deserve confirmation prompts)."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "window_days": {"type": "integer", "default": 7},
            "tool": {"type": "string",
                     "description": "Single tool name to focus on. Omit for all."},
        },
    }

    async def execute(self, window_days: int = 7, tool: Optional[str] = None) -> dict[str, Any]:
        return _stats(window_days=window_days, tool_name=tool)


tool = ToolReliabilityTool()
