"""Tool: context — "right now" ambient state in one call.

Aggregates the things the agent typically needs to ground a response:
  - local time + timezone offset + day-of-week
  - active window title (Windows-only via pygetwindow)
  - active focus session (project + intent + time elapsed)
  - top dispatch breadcrumb (most recently mentioned project)
  - next 1-2 calendar events (opt-in, requires Google auth)
  - last commit SHA + repo + subject
  - current weather (opt-in, requires Open-Meteo + city in user.md)
  - net cost spent today (USD)

Use at the START of a turn when the user's message is ambiguous
("¿qué hago?" / "ayuda" / "estoy perdido") so the agent grounds in real
state instead of guessing.

Risk: 0 — pure reads + opt-in network calls.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from kee.core import db
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_DAY_ES = {
    0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves",
    4: "viernes", 5: "sábado", 6: "domingo",
}


def _now_block() -> dict[str, Any]:
    now = datetime.now()
    utc_offset_hours = round(
        (datetime.now() - datetime.utcnow()).total_seconds() / 3600, 1,
    )
    return {
        "iso": now.isoformat(timespec="seconds"),
        "hour": now.hour,
        "minute": now.minute,
        "day_of_week_es": _DAY_ES[now.weekday()],
        "utc_offset_hours": utc_offset_hours,
    }


def _active_window() -> dict[str, Any]:
    try:
        import pygetwindow as gw  # type: ignore
        w = gw.getActiveWindow()
        if w:
            return {"title": getattr(w, "title", "") or "?"}
    except Exception:
        pass
    return {"title": None}


def _focus() -> dict[str, Any] | None:
    try:
        from kee.tools.focus import _current
        f = _current()
        if not f:
            return None
        elapsed = None
        try:
            started = f.get("started_at")
            if started and hasattr(started, "timestamp"):
                elapsed_min = int((datetime.now() - started).total_seconds() / 60)
                elapsed = elapsed_min
        except Exception:
            pass
        return {
            "project": f.get("project"),
            "intent": f.get("intent"),
            "elapsed_min": elapsed,
            "drift_count": f.get("drift_count", 0),
        }
    except Exception:
        return None


def _top_dispatch() -> dict[str, Any] | None:
    try:
        from kee.cognition import dispatch_registry as dr
        recent = dr.recent_dispatches(limit=1)
        if recent:
            r = recent[0]
            return {"project": r.get("project"),
                    "kind": r.get("kind"),
                    "summary": (r.get("summary") or "")[:120],
                    "ts": r.get("timestamp")}
    except Exception:
        pass
    return None


def _last_commit() -> dict[str, Any] | None:
    """Most recent commit across the repo set (any author)."""
    try:
        from kee.tools.commits import _find_repos, _git_log, _DEFAULT_ROOTS
        repos = _find_repos(_DEFAULT_ROOTS, max_depth=2)
        commits: list[dict] = []
        for r in repos:
            commits.extend(_git_log(r, since="3 days ago"))
        if not commits:
            return None
        commits.sort(key=lambda c: c["ts"], reverse=True)
        c = commits[0]
        return {"sha": c["sha"], "repo": c["repo"],
                "ts": c["ts"], "subject": c["subject"][:120]}
    except Exception:
        return None


def _cost_today() -> dict[str, Any] | None:
    """Total LLM spend (USD) since midnight local."""
    try:
        con = db.get_connection()
        row = con.execute(
            "SELECT SUM(cost_usd) FROM audit_log "
            "WHERE provider IS NOT NULL "
            "AND timestamp >= date('now', 'localtime')"
        ).fetchone()
        total = float(row[0] or 0)
        return {"usd": round(total, 4)}
    except Exception:
        return None


def _pending_callbacks(limit: int = 3) -> list[dict[str, Any]]:
    """Next few `scheduled_callbacks` so the agent knows what's coming."""
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, fire_at, kind, payload FROM scheduled_callbacks "
            "WHERE fired = 0 AND cancelled = 0 "
            "ORDER BY fire_at ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        out = []
        import json as _json
        for r in rows:
            try:
                payload = _json.loads(r[3] or "{}")
            except Exception:
                payload = {}
            out.append({
                "id": r[0],
                "fire_at": str(r[1]),
                "kind": r[2],
                "message": payload.get("message"),
            })
        return out
    except Exception:
        return []


class ContextTool(Tool):
    name = "context"
    description = (
        "Snapshot del estado ambiente AHORA: hora local, ventana activa, "
        "sesión de foco abierta, último dispatch, último commit, gasto LLM "
        "del día, próximos callbacks programados. (Opcional: clima y "
        "calendario.) Úsalo al INICIO de una respuesta cuando el mensaje "
        "del usuario es ambiguo ('¿qué hago?', 'ayúdame', '¿en qué iba?') "
        "para que la respuesta sea concreta y no genérica.\n"
        "NOT accepted: query — esto es snapshot, no búsqueda."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "include_calendar": {"type": "boolean", "default": False},
            "include_weather": {"type": "boolean", "default": False},
        },
    }

    async def execute(
        self,
        include_calendar: bool = False,
        include_weather: bool = False,
    ) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        now, win, focus, dispatch, commit, cost, callbacks = await asyncio.gather(
            loop.run_in_executor(None, _now_block),
            loop.run_in_executor(None, _active_window),
            loop.run_in_executor(None, _focus),
            loop.run_in_executor(None, _top_dispatch),
            loop.run_in_executor(None, _last_commit),
            loop.run_in_executor(None, _cost_today),
            loop.run_in_executor(None, _pending_callbacks, 3),
        )

        out: dict[str, Any] = {
            "now": now,
            "active_window": win,
            "focus": focus,
            "top_dispatch": dispatch,
            "last_commit": commit,
            "cost_today": cost,
            "pending_callbacks": callbacks,
        }

        if include_calendar:
            try:
                from kee.tools.calendar_tool import tool as cal
                out["calendar_next"] = await cal.execute(
                    action="upcoming", hours=12, max_results=2,
                )
            except Exception as e:
                out["calendar_next"] = {"error": str(e)}

        if include_weather:
            try:
                from kee.tools.weather import tool as wx
                out["weather"] = await wx.execute()
            except Exception as e:
                out["weather"] = {"error": str(e)}

        return out


tool = ContextTool()
