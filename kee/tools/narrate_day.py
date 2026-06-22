"""Tool: narrate_day — chronological narrative of a specific day.

Aggregates everything that happened on a given date into a single
markdown timeline:

  - commits (per repo, with subject)
  - dispatches (project mentions / agent breadcrumbs)
  - plans proposed + executed
  - focus sessions (when started, project, intent, drift, outcome)
  - notifications (urgent ones get bullet, low get count)
  - perception events (window titles + brief descriptions)
  - conversations summarized
  - agent QA degradation events (if any)

Optional `llm_rewrite=true` passes the structured timeline through
Ollama for a 2-paragraph narrative summary. Default is the raw timeline
(deterministic, $0).

Use case:
  - "qué hice ayer?"
  - "cuéntame el martes pasado"
  - end-of-day diary auto-fill
  - sleep cycle's morning brief — actually grounded in real events

Risk: 0 — pure SQL + git log reads.
"""

from __future__ import annotations

import json
import logging
import time as _time
from datetime import date, datetime, timedelta
from typing import Any

from kee.core import db
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _date_bounds(target: date) -> tuple[str, str]:
    """Return (start, end) ISO strings bracketing the day in local time."""
    start = datetime.combine(target, datetime.min.time())
    end = start + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


def _parse_target(spec: str | None) -> date:
    """Accept None|today|yesterday|YYYY-MM-DD."""
    if not spec or spec == "today":
        return date.today()
    if spec == "yesterday":
        return date.today() - timedelta(days=1)
    return datetime.strptime(spec, "%Y-%m-%d").date()


def _commits_for_day(target: date) -> list[dict]:
    """Walk the repo set for commits authored that day."""
    try:
        from kee.tools.commits import _find_repos, _git_log, _DEFAULT_ROOTS
    except Exception:
        return []
    since = target.isoformat() + " 00:00"
    until = (target + timedelta(days=1)).isoformat() + " 00:00"
    out = []
    for repo in _find_repos(_DEFAULT_ROOTS, max_depth=2):
        try:
            from kee.tools.commits import _git_bin
            import subprocess
            git = _git_bin()
            if not git:
                continue
            cmd = [git, "-C", str(repo), "log",
                   f"--since={since}", f"--until={until}",
                   "--pretty=format:%H%x09%ai%x09%an%x09%s",
                   "--no-merges"]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=15, encoding="utf-8",
                               errors="replace")
            if r.returncode != 0:
                continue
            for line in r.stdout.splitlines():
                parts = line.split("\t", 3)
                if len(parts) < 4:
                    continue
                sha, ts, author, subject = parts
                out.append({
                    "sha": sha[:8], "ts": ts, "author": author,
                    "subject": subject[:140], "repo": repo.name,
                })
        except Exception:
            continue
    # Dedup by SHA across mirror repos
    seen = {}
    for c in out:
        seen.setdefault(c["sha"], c)
    return sorted(seen.values(), key=lambda c: c["ts"])


def _dispatches_for_day(target: date) -> list[dict]:
    start, end = _date_bounds(target)
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT id, timestamp, project, kind, summary FROM dispatches "
            "WHERE timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp",
            (start, end),
        ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "ts": str(r[1]), "project": r[2],
         "kind": r[3], "summary": r[4] or ""}
        for r in rows
    ]


def _plans_for_day(target: date) -> list[dict]:
    start, end = _date_bounds(target)
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT id, timestamp, task, executed, outcome FROM plan_history "
            "WHERE timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp",
            (start, end),
        ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "ts": str(r[1]), "task": r[2],
         "executed": bool(r[3]), "outcome": r[4]}
        for r in rows
    ]


def _focus_for_day(target: date) -> list[dict]:
    start, end = _date_bounds(target)
    con = db.get_connection()
    try:
        # Sessions that started OR ended this day
        rows = con.execute(
            "SELECT id, started_at, project, intent, ended_at, "
            "outcome, drift_count FROM focus_sessions "
            "WHERE (started_at >= ? AND started_at < ?) "
            "   OR (ended_at IS NOT NULL "
            "       AND ended_at >= ? AND ended_at < ?) "
            "ORDER BY started_at",
            (start, end, start, end),
        ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "started_at": str(r[1]), "project": r[2],
         "intent": r[3], "ended_at": str(r[4]) if r[4] else None,
         "outcome": r[5], "drift_count": int(r[6] or 0)}
        for r in rows
    ]


def _notifications_for_day(target: date) -> list[dict]:
    start, end = _date_bounds(target)
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT id, timestamp, source, title, body, urgency "
            "FROM notifications "
            "WHERE timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp",
            (start, end),
        ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "ts": str(r[1]), "source": r[2],
         "title": r[3], "body": (r[4] or "")[:160],
         "urgency": int(r[5] or 1)}
        for r in rows
    ]


def _perception_for_day(target: date) -> list[dict]:
    start, end = _date_bounds(target)
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT id, timestamp, parameters FROM audit_log "
            "WHERE action='perception_screenshot' "
            "AND timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp",
            (start, end),
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            p = json.loads(r[2] or "{}")
        except Exception:
            continue
        out.append({
            "id": r[0], "ts": str(r[1]),
            "window": p.get("window_title", "?"),
            "description": p.get("description", "")[:200],
        })
    return out


def _conversations_for_day(target: date) -> list[dict]:
    start, end = _date_bounds(target)
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT id, summary, last_active, source FROM conversations "
            "WHERE summary IS NOT NULL AND summary != '' "
            "AND last_active >= ? AND last_active < ?",
            (start, end),
        ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "summary": r[1], "last_active": str(r[2]),
         "source": r[3] or "?"}
        for r in rows
    ]


def _format_markdown(target: date, data: dict[str, list]) -> str:
    """Compose a chronological markdown narrative."""
    lines = [f"# {target.isoformat()} — narrate_day", ""]

    n_items = sum(len(v) for v in data.values())
    if n_items == 0:
        lines.append(f"_Nada registrado el {target.isoformat()}._")
        return "\n".join(lines)

    # Headline counts
    counts = []
    if data.get("commits"):
        counts.append(f"{len(data['commits'])} commit(s)")
    if data.get("dispatches"):
        counts.append(f"{len(data['dispatches'])} dispatch(es)")
    if data.get("plans"):
        counts.append(f"{len(data['plans'])} plan(es)")
    if data.get("focus"):
        counts.append(f"{len(data['focus'])} focus session(s)")
    if data.get("notifications"):
        counts.append(f"{len(data['notifications'])} notification(s)")
    if data.get("perception"):
        counts.append(f"{len(data['perception'])} perception event(s)")
    if data.get("conversations"):
        counts.append(f"{len(data['conversations'])} conversation(s)")
    if counts:
        lines.append("**Resumen:** " + ", ".join(counts) + ".")
        lines.append("")

    # Commits section
    if data.get("commits"):
        lines.append("## Commits")
        for c in data["commits"]:
            t = c["ts"][11:16] if len(c["ts"]) > 16 else c["ts"]
            lines.append(f"- `{t}` `{c['sha']}` `{c['repo']}` — {c['subject']}")
        lines.append("")

    # Focus sessions
    if data.get("focus"):
        lines.append("## Focus sessions")
        for f in data["focus"]:
            t_start = f["started_at"][11:16] if len(f["started_at"]) > 16 else f["started_at"]
            tag = (f"→ outcome: {f['outcome'] or '?'}"
                   if f["ended_at"] else "(still open)")
            drift = f"  [drift={f['drift_count']}]" if f["drift_count"] else ""
            lines.append(
                f"- `{t_start}` **{f['project']}** "
                f"({f['intent'] or '?'}){drift} {tag}"
            )
        lines.append("")

    # Plans
    if data.get("plans"):
        lines.append("## Plans")
        for p in data["plans"]:
            t = p["ts"][11:16] if len(p["ts"]) > 16 else p["ts"]
            mark = "[x]" if p["executed"] else "[ ]"
            lines.append(f"- `{t}` {mark} {p['task']}")
        lines.append("")

    # Dispatches
    if data.get("dispatches"):
        lines.append("## Project breadcrumbs")
        for d in data["dispatches"]:
            t = d["ts"][11:16] if len(d["ts"]) > 16 else d["ts"]
            lines.append(
                f"- `{t}` **{d['project']}** [{d['kind']}]: "
                f"{d['summary'][:120]}"
            )
        lines.append("")

    # Notifications
    if data.get("notifications"):
        urgent = [n for n in data["notifications"] if n["urgency"] >= 2]
        normal = [n for n in data["notifications"] if n["urgency"] == 1]
        low = [n for n in data["notifications"] if n["urgency"] == 0]
        lines.append("## Notifications")
        if urgent:
            lines.append("**Critical:**")
            for n in urgent[:5]:
                t = n["ts"][11:16] if len(n["ts"]) > 16 else n["ts"]
                lines.append(f"- `{t}` {n['title']}: {n['body'][:80]}")
        if normal:
            lines.append(f"_{len(normal)} normal_, _{len(low)} low_")
        lines.append("")

    # Perception events
    if data.get("perception"):
        lines.append("## Passive perception (apps you used)")
        seen_windows = set()
        for p in data["perception"]:
            w = p["window"]
            if w in seen_windows:
                continue
            seen_windows.add(w)
            t = p["ts"][11:16] if len(p["ts"]) > 16 else p["ts"]
            lines.append(f"- `{t}` **{w[:60]}** — {p['description'][:100]}")
            if len(seen_windows) >= 8:
                break
        lines.append("")

    # Conversations
    if data.get("conversations"):
        lines.append("## Conversations")
        for c in data["conversations"]:
            lines.append(
                f"- [{c['source']}] {c['summary'][:160]}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


class NarrateDayTool(Tool):
    name = "narrate_day"
    description = (
        "Línea-de-tiempo en markdown de TODO lo que pasó un día específico: "
        "commits, dispatches, planes, focus sessions, notificaciones, "
        "perception events, y conversaciones. Determinístico, sin LLM "
        "(zero-cost). Ideal para 'qué hice ayer', morning brief grounded "
        "en eventos reales, o llenado de diario.\n"
        "Date input: 'today' (default), 'yesterday', o 'YYYY-MM-DD'.\n"
        "Devuelve `{markdown, raw, counts}` donde `raw` es el dict crudo "
        "y `counts` el resumen numérico."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "today | yesterday | YYYY-MM-DD (default today)",
            },
            "save_to_vault": {
                "type": "boolean", "default": False,
                "description": "Escribir a vault/_kee/daily/<date>-narrative.md",
            },
        },
    }

    async def execute(
        self,
        date: str | None = None,
        save_to_vault: bool = False,
    ) -> dict[str, Any]:
        try:
            target = _parse_target(date)
        except Exception as e:
            return {"ok": False, "error": f"bad date: {e}"}

        t0 = _time.time()
        data = {
            "commits": _commits_for_day(target),
            "dispatches": _dispatches_for_day(target),
            "plans": _plans_for_day(target),
            "focus": _focus_for_day(target),
            "notifications": _notifications_for_day(target),
            "perception": _perception_for_day(target),
            "conversations": _conversations_for_day(target),
        }
        markdown = _format_markdown(target, data)
        elapsed_ms = int((_time.time() - t0) * 1000)

        out: dict[str, Any] = {
            "ok": True,
            "date": target.isoformat(),
            "elapsed_ms": elapsed_ms,
            "counts": {k: len(v) for k, v in data.items()},
            "markdown": markdown,
            "raw": data,
        }

        if save_to_vault:
            try:
                from kee.config import settings
                out_dir = settings.vault_dir / "_kee" / "daily"
                out_dir.mkdir(parents=True, exist_ok=True)
                p = out_dir / f"{target.isoformat()}-narrative.md"
                p.write_text(markdown, encoding="utf-8")
                out["saved_to"] = str(p)
            except Exception as e:
                out["save_error"] = str(e)
        return out


tool = NarrateDayTool()
