"""Tool: reflect — agent-callable mid-day reflection.

Pulls the introspection data sources Kee accumulates (conversation_qa,
plan_history, dispatch_registry, tool_reliability, kwarg hallucinations)
into a single structured snapshot. Used by:

  - the agent itself, when Coco asks "cómo vas hoy" or "qué llevas hecho"
    (replies grounded in real numbers instead of vibes)
  - heartbeat / proactive triggers ("voice score dropped 0.2 → tell Coco")
  - Sleep Cycle's morning brief

Risk: 0 — all reads.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from kee.core import db
from kee.tools.base import Tool


def _qa_snapshot(window_days: int) -> dict[str, Any]:
    """Cross-process QA roll-up from `conversation_qa` audit rows."""
    import json as _json
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT tool_name, parameters FROM audit_log "
            "WHERE action='conversation_qa' "
            "AND timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
    except Exception:
        rows = []
    by_source: dict[str, list[float]] = {}
    for src, raw in rows:
        try:
            payload = _json.loads(raw or "{}")
            score = float(payload.get("score") or 0)
            by_source.setdefault(src or "?", []).append(score)
        except Exception:
            continue
    summary = {}
    for src, scores in by_source.items():
        summary[src] = {
            "n": len(scores),
            "avg": round(sum(scores) / len(scores), 3) if scores else None,
            "min": round(min(scores), 3) if scores else None,
        }
    total = sum(len(v) for v in by_source.values())
    return {
        "samples": total,
        "avg_score": (round(
            sum(s for v in by_source.values() for s in v) / total, 3,
        ) if total else None),
        "by_source": summary,
    }


def _plan_snapshot(window_days: int) -> dict[str, Any]:
    con = db.get_connection()
    try:
        n_total = con.execute(
            "SELECT COUNT(*) FROM plan_history "
            "WHERE timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchone()[0]
        n_exec = con.execute(
            "SELECT COUNT(*) FROM plan_history "
            "WHERE timestamp >= datetime('now', ? || ' days') AND executed = 1",
            (f"-{int(window_days)}",),
        ).fetchone()[0]
        recent = con.execute(
            "SELECT id, task, executed FROM plan_history "
            "WHERE timestamp >= datetime('now', ? || ' days') "
            "ORDER BY id DESC LIMIT 5",
            (f"-{int(window_days)}",),
        ).fetchall()
    except Exception:
        return {"total": 0, "executed": 0, "execution_rate": None,
                "recent": []}
    return {
        "total": n_total,
        "executed": n_exec,
        "pending": max(0, n_total - n_exec),
        "execution_rate": (round(n_exec / n_total, 3)
                           if n_total else None),
        "recent": [
            {"id": r[0], "task": (r[1] or "")[:90],
             "executed": bool(r[2])}
            for r in recent
        ],
    }


def _hallucination_snapshot(window_days: int) -> dict[str, Any]:
    import json as _json
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT tool_name, parameters FROM audit_log "
            "WHERE action='kwarg_hallucination' "
            "AND timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
    except Exception:
        rows = []
    by_tool: dict[str, Counter] = {}
    for tool_name, raw in rows:
        if not tool_name or not raw:
            continue
        try:
            payload = _json.loads(raw)
        except Exception:
            continue
        c = by_tool.setdefault(tool_name, Counter())
        for u in (payload.get("unknown") or []):
            c[u] += 1
    return {
        "total": len(rows),
        "by_tool": [
            {"tool": name, "count": sum(c.values()),
             "top_kwargs": c.most_common(3)}
            for name, c in sorted(by_tool.items(),
                                  key=lambda kv: sum(kv[1].values()),
                                  reverse=True)[:5]
        ],
    }


def _tool_bottom(window_days: int, top_n: int = 3) -> list[dict[str, Any]]:
    """Lowest-trust tools (Wilson-corrected)."""
    from kee.tools.tool_reliability import _stats as reliability_stats
    rep = reliability_stats(window_days=window_days)
    rows = rep.get("tools") or []
    # Filter out zero-call rows so we rank by real usage.
    rows = [r for r in rows if r.get("calls", 0) >= 3]
    rows.sort(key=lambda r: r.get("trust_score", 1.0))
    return [
        {"tool": r["tool"], "trust": r["trust_score"],
         "calls": r["calls"], "rate": r["success_rate"]}
        for r in rows[:top_n]
    ]


def _summary_line(payload: dict[str, Any]) -> str:
    parts = []
    qa = payload.get("qa") or {}
    if qa.get("samples"):
        parts.append(f"voz/chat avg score {qa['avg_score']} ({qa['samples']} muestras)")
    plans = payload.get("plans") or {}
    if plans.get("total"):
        parts.append(
            f"{plans['executed']}/{plans['total']} planes ejecutados "
            f"({int((plans.get('execution_rate') or 0) * 100)}%)"
        )
    halluc = payload.get("hallucinations") or {}
    if halluc.get("total"):
        parts.append(f"{halluc['total']} kwargs alucinados")
    bottom = payload.get("tool_bottom") or []
    if bottom:
        parts.append(
            f"tool más débil: {bottom[0]['tool']} (trust {bottom[0]['trust']})"
        )
    proj = payload.get("projects") or []
    if proj:
        parts.append("proyectos vivos: " +
                     ", ".join(p["project"] for p in proj[:3]))
    commits = payload.get("commits") or {}
    today_n = (commits.get("today") or {}).get("count")
    if today_n is not None:
        parts.append(f"commits hoy: {today_n}")
    inbox = payload.get("inbox") or {}
    totals = inbox.get("totals") or {}
    if totals.get("urgent"):
        parts.append(f"correos urgentes: {totals['urgent']}")
    return "; ".join(parts) if parts else "Nada nuevo."


class ReflectTool(Tool):
    name = "reflect"
    description = (
        "Snapshot estructurado del estado reciente de Kee — combina "
        "QA score por surface, planes ejecutados vs pendientes, "
        "alucinaciones de kwargs, tools menos confiables (Wilson), y "
        "proyectos activos. Úsalo cuando Coco pregunte 'cómo vas', "
        "'qué llevas hecho', o antes de decidir escalar a un modelo "
        "más caro. NO genera prosa; devuelve datos crudos + un "
        "`summary` de una línea para citar.\n"
        "NOT accepted: query, message — esto NO es un chat."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "window_days": {"type": "integer", "default": 7},
            "include_recent_plans": {"type": "boolean", "default": True},
            "include_commits": {
                "type": "boolean", "default": False,
                "description": "Also fold in `commits today` summary.",
            },
            "include_inbox": {
                "type": "boolean", "default": False,
                "description": "Also fold in `inbox_triage` totals (Gmail).",
            },
        },
    }

    async def execute(
        self,
        window_days: int = 7,
        include_recent_plans: bool = True,
        include_commits: bool = False,
        include_inbox: bool = False,
    ) -> dict[str, Any]:
        # Run independent reads in a thread pool — none are async.
        loop = asyncio.get_event_loop()
        qa, plans, halluc, bottom = await asyncio.gather(
            loop.run_in_executor(None, _qa_snapshot, window_days),
            loop.run_in_executor(None, _plan_snapshot, window_days),
            loop.run_in_executor(None, _hallucination_snapshot, window_days),
            loop.run_in_executor(None, _tool_bottom, window_days, 3),
        )
        from kee.cognition import dispatch_registry as dr
        try:
            projects = dr.active_projects(limit=5, days=window_days)
        except Exception:
            projects = []

        if not include_recent_plans:
            plans.pop("recent", None)

        out: dict[str, Any] = {
            "window_days": window_days,
            "qa": qa,
            "plans": plans,
            "hallucinations": halluc,
            "tool_bottom": bottom,
            "projects": projects,
        }

        # Optional fold-ins for the "morning brief" use case.
        if include_commits:
            try:
                from kee.tools.commits import tool as commits_tool
                today = await commits_tool.execute(
                    action="today", summary_only=True,
                )
                week = await commits_tool.execute(
                    action="week", summary_only=True,
                )
                out["commits"] = {
                    "today": {"count": today.get("count", 0),
                              "by_repo": today.get("by_repo", {})},
                    "week": {"count": week.get("count", 0),
                             "by_repo": week.get("by_repo", {})},
                }
            except Exception:
                out["commits"] = {"error": "commits tool failed"}
        if include_inbox:
            try:
                from kee.tools.inbox_triage import tool as inbox_tool
                inbox = await inbox_tool.execute(max_results=30)
                if inbox.get("ok"):
                    out["inbox"] = {
                        "scanned": inbox.get("scanned", 0),
                        "totals": inbox.get("totals", {}),
                    }
                else:
                    out["inbox"] = {"error": inbox.get("error")}
            except Exception as e:
                out["inbox"] = {"error": str(e)}

        out["summary"] = _summary_line(out)
        return out


tool = ReflectTool()
