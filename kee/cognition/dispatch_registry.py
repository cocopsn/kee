"""Dispatch registry — cross-session project + task awareness.

Ported from `bertrandmbanwi/Jarvis::core/dispatch_registry.py`. Tracks
which projects Coco has worked on across sessions so Kee can surface
context in the system prompt without him having to re-explain.

Schema (added to `data/kee.db`):
  dispatches    — one row per "Kee, work on X" event
  task_log      — granular log of each tool call against a project
  usage_patterns — derived stats (total tasks, last_active, common tools)

API:
  - record_dispatch(project, kind, summary)
  - log_task(project, tool_name, success, ...)
  - active_projects(limit=5)        → projects touched in last 7 days
  - recent_dispatches(limit=10)
  - format_for_prompt() → injectable text block ("Active projects: …")

Cost: 0. Used by `kee/core/identity.py::build_system_prompt` (when
wired) to give the model awareness of what the user is currently
working on.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kee.core import db


def ensure_schema() -> None:
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dispatches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            project     TEXT NOT NULL,
            kind        TEXT NOT NULL,         -- 'work' | 'review' | 'fix' | 'plan'
            summary     TEXT,
            metadata    TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_project ON dispatches(project)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_ts ON dispatches(timestamp)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            project     TEXT,
            tool_name   TEXT,
            success     INTEGER,
            elapsed_ms  INTEGER,
            note        TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_task_project ON task_log(project, timestamp)")
    conn.commit()


def record_dispatch(project: str, kind: str = "work", summary: str = "",
                    metadata: dict | None = None) -> int:
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO dispatches (timestamp, project, kind, summary, metadata)
        VALUES (datetime('now'), ?, ?, ?, ?)
    """, (project, kind, summary, json.dumps(metadata or {})))
    conn.commit()
    return cur.lastrowid


def log_task(project: str | None, tool_name: str, success: bool,
             elapsed_ms: int = 0, note: str = "") -> int:
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO task_log (timestamp, project, tool_name, success, elapsed_ms, note)
        VALUES (datetime('now'), ?, ?, ?, ?, ?)
    """, (project, tool_name, 1 if success else 0, int(elapsed_ms), note))
    conn.commit()
    return cur.lastrowid


def active_projects(limit: int = 5, days: int = 7) -> list[dict]:
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT project, COUNT(*) as touches, MAX(timestamp) as last_seen
        FROM dispatches
        WHERE timestamp >= datetime('now', '-{int(days)} days')
        GROUP BY project
        ORDER BY last_seen DESC
        LIMIT ?
    """, (limit,))
    return [{"project": r[0], "touches": r[1], "last_seen": r[2]}
            for r in cur.fetchall()]


def recent_dispatches(limit: int = 10) -> list[dict]:
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, timestamp, project, kind, summary
        FROM dispatches ORDER BY id DESC LIMIT ?
    """, (limit,))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def project_task_summary(project: str, days: int = 14) -> dict:
    """Per-project stats: tools used, success %, elapsed time."""
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT tool_name, success, elapsed_ms
        FROM task_log
        WHERE project = ? AND timestamp >= datetime('now', '-{int(days)} days')
    """, (project,))
    rows = cur.fetchall()
    if not rows:
        return {"project": project, "tasks": 0, "success_rate": None, "tools": []}
    n = len(rows)
    ok = sum(1 for r in rows if r[1])
    total_ms = sum(r[2] or 0 for r in rows)
    from collections import Counter
    tools = Counter(r[0] for r in rows).most_common(5)
    return {
        "project": project,
        "tasks": n,
        "success_rate": round(ok / n, 2),
        "total_elapsed_ms": total_ms,
        "tools": tools,
    }


def format_for_prompt(max_chars: int = 800) -> str:
    """Markdown block to inject into the system prompt so the model
    knows what Coco is currently working on. Empty string if nothing
    recent."""
    actives = active_projects(limit=4, days=7)
    recents = recent_dispatches(limit=4)
    if not actives and not recents:
        return ""
    lines = ["## Recent project context (Kee's memory)"]
    if actives:
        lines.append("Active projects (last 7 days):")
        for a in actives:
            lines.append(f"  - {a['project']} — {a['touches']} touches, last {a['last_seen']}")
    if recents:
        lines.append("")
        lines.append("Last few dispatches:")
        for r in recents:
            summ = (r["summary"] or "")[:80]
            lines.append(f"  - [{r['kind']}] {r['project']}: {summ}")
    out = "\n".join(lines)
    return out[:max_chars]
