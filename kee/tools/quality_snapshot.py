"""Tool: quality_snapshot — agent-introspection over its own response quality.

Wraps `kee.cognition.conversation_monitor.snapshot()` so the agent (or
Sleep Cycle / heartbeat) can ask "how am I doing lately?" without needing
an HTTP round-trip. Use to:

  - bias the next decision (e.g. if voice avg < 0.6 → switch to chat
    surface, or escalate to a heavier model)
  - feed Sleep Cycle's nightly axiom synthesis ("voice quality dropped 0.2
    after the new TTS voice → revert")
  - explain low-confidence behaviour to Coco when asked

Risk: 0 — pure read of an in-memory deque + a tiny stats roll-up.
"""

from __future__ import annotations

from typing import Any

from kee.cognition.conversation_monitor import snapshot as _snapshot
from kee.core import db
from kee.tools.base import Tool


def _lifetime_snapshot(window_days: int = 7) -> dict[str, Any]:
    """Read `conversation_qa` rows from audit_log so cross-process surfaces
    (dashboard, telegram bot, voice) all share quality history."""
    import json as _json
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT timestamp, tool_name, parameters FROM audit_log "
            "WHERE action='conversation_qa' "
            "AND timestamp >= datetime('now', ? || ' days') "
            "ORDER BY id DESC LIMIT 500",
            (f"-{int(window_days)}",),
        ).fetchall()
    except Exception as e:
        return {"window_days": window_days, "error": str(e)}
    by_source: dict[str, dict] = {}
    scores: list[float] = []
    for ts, source, raw in rows:
        if not raw:
            continue
        try:
            payload = _json.loads(raw)
        except Exception:
            continue
        score = float(payload.get("score") or 0)
        scores.append(score)
        d = by_source.setdefault(source or "?", {"n": 0, "sum": 0.0})
        d["n"] += 1; d["sum"] += score
    n = len(scores)
    avg = round(sum(scores) / n, 3) if n else None
    return {
        "window_days": window_days,
        "count": n,
        "avg_score": avg,
        "by_source": {
            src: {"count": v["n"], "avg_score": round(v["sum"] / v["n"], 2)}
            for src, v in by_source.items()
        },
    }


def _summarize(snap: dict) -> str:
    n = snap.get("count", 0)
    if not n:
        return "No replies observed yet this process."
    avg = snap.get("avg_score")
    trend = snap.get("trend", 0)
    parts = [f"{n} samples, avg score {avg}"]
    if trend > 0.05:
        parts.append(f"trending up (+{trend:.2f})")
    elif trend < -0.05:
        parts.append(f"trending down ({trend:.2f})")
    by = snap.get("by_source") or {}
    if by:
        bs = ", ".join(
            f"{src}={b['avg_score']} (n={b['count']})"
            for src, b in by.items()
        )
        parts.append(f"per-surface: {bs}")
    return " — ".join(parts) + "."


class QualitySnapshotTool(Tool):
    name = "quality_snapshot"
    description = (
        "Inspect Kee's own recent reply quality (last 20 turns, in-memory). "
        "Returns avg score 0-1, 5-vs-prev-5 trend, per-surface breakdown, "
        "and the most recent issues per sample. Use BEFORE deciding "
        "whether to escalate to a heavier model, switch surface, or warn "
        "Coco that voice mode is degraded."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "include_recent": {
                "type": "boolean",
                "default": True,
                "description": "Include the last 10 raw samples (false = "
                               "summary only, smaller payload).",
            },
            "lifetime": {
                "type": "boolean",
                "default": False,
                "description": "Also include a cross-process roll-up read "
                               "from `audit_log` (last `window_days`).",
            },
            "window_days": {
                "type": "integer",
                "default": 7,
                "description": "Window for the lifetime view (read-only).",
            },
        },
    }

    async def execute(
        self,
        include_recent: bool = True,
        lifetime: bool = False,
        window_days: int = 7,
    ) -> dict[str, Any]:
        snap = _snapshot()
        out: dict[str, Any] = {
            "summary": _summarize(snap),
            "count": snap.get("count", 0),
            "avg_score": snap.get("avg_score"),
            "trend": snap.get("trend", 0),
            "by_source": snap.get("by_source", {}),
        }
        if include_recent:
            out["recent"] = snap.get("recent", [])
        if lifetime:
            out["lifetime"] = _lifetime_snapshot(window_days=window_days)
        return out


tool = QualitySnapshotTool()
