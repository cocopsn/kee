"""Tool: focus — declare + track current work focus.

Coco (or the agent on his behalf) opens a focus session: "I'm working on
AUCTORUM landing for 90 minutes". The session lives in
`focus_sessions(project, intent, deadline, …)`. The heartbeat's
`_check_focus_drift` (added separately) periodically compares the active
window title against the session's `project` and bumps `drift_count` if
attention strays — that signal feeds Sleep Cycle and the dashboard.

Single-active invariant: starting a new session auto-ends any open one.

Risk: 0 — own table only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from kee.core import db
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict[str, Any]:
    cols = ["id", "started_at", "project", "intent", "deadline",
            "ended_at", "outcome", "drift_count"]
    return {c: row[i] for i, c in enumerate(cols)}


def _current() -> dict | None:
    con = db.get_connection()
    row = con.execute(
        "SELECT id, started_at, project, intent, deadline, "
        "ended_at, outcome, drift_count "
        "FROM focus_sessions "
        "WHERE ended_at IS NULL "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row) if row else None


def _end_active(outcome: str | None = None,
                auto_retro: bool = False) -> int | None:
    """Close the open session if any. Returns its id or None.

    When `auto_retro=True` and the caller didn't supply an outcome, run a
    deterministic post-mortem: count commits across `commits` tool's repo
    set during the focus window, and write
    `"<N commits across <repos>: <subjects[:3]>"` as the outcome.
    """
    cur_active = _current()
    if not cur_active:
        return None
    final_outcome = outcome
    if auto_retro and not final_outcome:
        final_outcome = _compose_retro(cur_active)
    with db.cursor() as cur:
        cur.execute(
            "UPDATE focus_sessions "
            "SET ended_at = CURRENT_TIMESTAMP, "
            "    outcome = COALESCE(?, outcome) "
            "WHERE id = ?",
            (final_outcome, cur_active["id"]),
        )
    return cur_active["id"]


def _compose_retro(active: dict) -> str | None:
    """Build a one-line outcome from commits during the focus window.

    Returns None if no commits landed (so the caller can leave the field
    NULL instead of writing "0 commits" noise).
    """
    started = active.get("started_at")
    if not started:
        return None
    # Convert datetime → "YYYY-MM-DD HH:MM:SS" for git --since
    if hasattr(started, "strftime"):
        since = started.strftime("%Y-%m-%d %H:%M:%S")
    else:
        since = str(started)
    try:
        from kee.tools.commits import _find_repos, _git_log, _DEFAULT_ROOTS
        commits: list[dict] = []
        for repo in _find_repos(_DEFAULT_ROOTS, max_depth=2):
            commits.extend(_git_log(repo, since=since))
    except Exception:
        commits = []
    if not commits:
        return None
    repos = sorted({c["repo"] for c in commits})
    subjects = [c["subject"][:50] for c in commits[:3]]
    return (
        f"{len(commits)} commit(s) across {','.join(repos)}: "
        f"{' | '.join(subjects)}"
    )


def _start(project: str, intent: str | None,
           duration_min: int | None) -> dict[str, Any]:
    # Atomically end any open session before starting a new one.
    closed_id = _end_active(outcome="auto-closed by new focus.start")
    deadline = None
    if duration_min:
        # SQLite TIMESTAMP affinity wants "YYYY-MM-DD HH:MM:SS" — no T.
        deadline = (datetime.now()
                    + timedelta(minutes=int(duration_min))).strftime(
            "%Y-%m-%d %H:%M:%S",
        )
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO focus_sessions (project, intent, deadline) "
            "VALUES (?, ?, ?)",
            (project, intent, deadline),
        )
        new_id = cur.lastrowid
    return {
        "ok": True, "id": new_id, "project": project, "intent": intent,
        "deadline": deadline, "auto_closed_id": closed_id,
    }


def _bump_drift(reason: str | None = None) -> dict[str, Any]:
    cur_active = _current()
    if not cur_active:
        return {"ok": False, "error": "no active focus"}
    with db.cursor() as cur:
        cur.execute(
            "UPDATE focus_sessions "
            "SET drift_count = drift_count + 1 "
            "WHERE id = ?",
            (cur_active["id"],),
        )
    return {"ok": True, "id": cur_active["id"],
            "drift_count": cur_active["drift_count"] + 1, "reason": reason}


def _history(limit: int = 10) -> list[dict]:
    con = db.get_connection()
    rows = con.execute(
        "SELECT id, started_at, project, intent, deadline, "
        "ended_at, outcome, drift_count "
        "FROM focus_sessions ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


class FocusTool(Tool):
    name = "focus"
    description = (
        "Declarar y seguir la sesión de foco actual. Coco dice 'voy a "
        "trabajar en AUCTORUM 90 min', tú llamas `focus start project="
        "auctorum duration_min=90`. El heartbeat después monitorea si la "
        "ventana activa empata con `project` y bumpea `drift_count` si no.\n"
        "Acciones:\n"
        "  - 'start':   abre sesión (cierra la previa si había). project "
        "               required; intent + duration_min opcionales.\n"
        "  - 'end':     cierra la sesión actual con outcome opcional.\n"
        "  - 'current': sesión activa o null.\n"
        "  - 'history': últimas N sesiones.\n"
        "  - 'drift':   bumpea drift_count manualmente (uso interno del "
        "               heartbeat).\n"
        "Solo UNA sesión activa al tiempo."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "end", "current", "history", "drift"],
                "default": "current",
            },
            "project": {"type": "string"},
            "intent": {"type": "string"},
            "duration_min": {"type": "integer"},
            "outcome": {"type": "string"},
            "auto_retro": {
                "type": "boolean", "default": False,
                "description": "On end: when no outcome given, build one "
                               "from commits during the focus window.",
            },
            "limit": {"type": "integer", "default": 10},
            "reason": {"type": "string",
                       "description": "Drift reason (drift only)."},
        },
    }

    async def execute(
        self,
        action: str = "current",
        project: str | None = None,
        intent: str | None = None,
        duration_min: int | None = None,
        outcome: str | None = None,
        limit: int = 10,
        reason: str | None = None,
        auto_retro: bool = False,
    ) -> dict[str, Any]:
        if action == "current":
            c = _current()
            return {"ok": True, "active": c}
        if action == "start":
            if not project:
                return {"ok": False, "error": "project required"}
            return _start(project, intent, duration_min)
        if action == "end":
            closed = _end_active(outcome=outcome, auto_retro=auto_retro)
            if closed is None:
                return {"ok": False, "error": "no active focus to end"}
            # Re-read so we surface the (possibly auto-composed) outcome.
            con = db.get_connection()
            row = con.execute(
                "SELECT outcome FROM focus_sessions WHERE id = ?",
                (closed,),
            ).fetchone()
            return {"ok": True, "id": closed,
                    "outcome": row[0] if row else None}
        if action == "history":
            return {"ok": True, "sessions": _history(limit=limit)}
        if action == "drift":
            return _bump_drift(reason)
        return {"ok": False, "error": f"unknown action {action!r}"}


tool = FocusTool()
