"""Tool: smart_search — unified search across Kee's data surfaces.

Sources:
  - `messages` table (substring) — what was literally said in past chats
  - `plan_history.task` (substring) — what got planned
  - `dispatches.summary` (substring) — project breadcrumbs
  - `audit_log.action` (substring on tool_name) — what tools fired
  - `notifications.title/body` (substring) — what fired at Coco
  - vault notes (semantic via `memory_search`, opt-in)

Returns a flat ranked list of hits with `source` tags so the agent can
cite where each match came from. Use this BEFORE asking the user "what
were we doing?" — it's much cheaper than reaching for `claude_code` and
gives the LLM real context to ground its answer.

Risk: 0 — read-only over our own DB.
"""

from __future__ import annotations

import logging
from typing import Any

from kee.core import db
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _search_messages(query: str, limit: int) -> list[dict[str, Any]]:
    if not query:
        return []
    con = db.get_connection()
    rows = con.execute(
        "SELECT id, conversation_id, role, content, created_at "
        "FROM messages WHERE content LIKE ? "
        "ORDER BY id DESC LIMIT ?",
        (f"%{query}%", int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        snippet = (r[3] or "").replace("\n", " ")
        idx = snippet.lower().find(query.lower())
        if idx == -1:
            snippet = snippet[:140]
        else:
            start = max(0, idx - 50)
            end = min(len(snippet), idx + len(query) + 90)
            snippet = ("…" if start > 0 else "") + snippet[start:end] + (
                "…" if end < len(r[3] or "") else "")
        ts = r[4]
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        out.append({
            "source": "message",
            "ref": f"msg:{r[0]}",
            "conversation_id": r[1],
            "role": r[2],
            "snippet": snippet,
            "ts": ts,
        })
    return out


def _search_plans(query: str, limit: int) -> list[dict[str, Any]]:
    if not query:
        return []
    con = db.get_connection()
    rows = con.execute(
        "SELECT id, timestamp, task, executed FROM plan_history "
        "WHERE task LIKE ? ORDER BY id DESC LIMIT ?",
        (f"%{query}%", int(limit)),
    ).fetchall()
    return [
        {
            "source": "plan",
            "ref": f"plan:{r[0]}",
            "executed": bool(r[3]),
            "snippet": r[2],
            "ts": str(r[1]),
        }
        for r in rows
    ]


def _search_dispatches(query: str, limit: int) -> list[dict[str, Any]]:
    if not query:
        return []
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT id, timestamp, project, kind, summary FROM dispatches "
            "WHERE project LIKE ? OR summary LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", int(limit)),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "source": "dispatch",
            "ref": f"dispatch:{r[0]}",
            "project": r[2],
            "kind": r[3],
            "snippet": r[4] or f"[{r[3]}] {r[2]}",
            "ts": r[1],
        }
        for r in rows
    ]


def _search_notifications(query: str, limit: int) -> list[dict[str, Any]]:
    if not query:
        return []
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT id, timestamp, source, title, body, urgency "
            "FROM notifications "
            "WHERE title LIKE ? OR body LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", int(limit)),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "source": "notification",
            "ref": f"notif:{r[0]}",
            "snippet": f"{r[3]}: {(r[4] or '')[:120]}",
            "kind": r[2],
            "urgency": r[5],
            "ts": str(r[1]),
        }
        for r in rows
    ]


def _search_audit_tools(query: str, limit: int) -> list[dict[str, Any]]:
    """Search by tool_name only — useful for 'when did I last call X'."""
    if not query:
        return []
    con = db.get_connection()
    rows = con.execute(
        "SELECT id, timestamp, action, tool_name, success "
        "FROM audit_log WHERE tool_name LIKE ? "
        "ORDER BY id DESC LIMIT ?",
        (f"%{query}%", int(limit)),
    ).fetchall()
    return [
        {
            "source": "tool_call",
            "ref": f"audit:{r[0]}",
            "snippet": f"{r[3]} [{r[2]}] success={bool(r[4])}",
            "ts": str(r[1]),
        }
        for r in rows
    ]


class SmartSearchTool(Tool):
    name = "smart_search"
    description = (
        "Búsqueda unificada sobre todo lo que Kee almacena: messages, "
        "plan_history, dispatches, notifications, y opcionalmente "
        "tool_calls del audit y notas del vault. Devuelve una lista plana "
        "rankeada con `source` etiquetado para citar la procedencia.\n"
        "Úsalo CUANDO no estés seguro qué tabla buscar y necesites barrido "
        "amplio. Para búsqueda específica usa `recall` (mensajes), "
        "`plan recall` (planes), `dispatch recent` (proyectos), o "
        "`memory_search` (vault RAG)."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "sources": {
                "type": "array",
                "items": {"type": "string",
                          "enum": ["messages", "plans", "dispatches",
                                   "notifications", "tool_calls", "vault"]},
                "description": "Limit search to these sources. Default: "
                               "messages + plans + dispatches + notifications.",
            },
            "limit_per_source": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(
        self,
        query: str,
        sources: list[str] | None = None,
        limit_per_source: int = 5,
    ) -> dict[str, Any]:
        if not query or not query.strip():
            return {"ok": False, "error": "query required"}
        active = set(sources or [
            "messages", "plans", "dispatches", "notifications",
        ])
        hits: list[dict[str, Any]] = []
        if "messages" in active:
            hits.extend(_search_messages(query, limit_per_source))
        if "plans" in active:
            hits.extend(_search_plans(query, limit_per_source))
        if "dispatches" in active:
            hits.extend(_search_dispatches(query, limit_per_source))
        if "notifications" in active:
            hits.extend(_search_notifications(query, limit_per_source))
        if "tool_calls" in active:
            hits.extend(_search_audit_tools(query, limit_per_source))
        if "vault" in active:
            try:
                from kee.core import services
                if services.memory is not None:
                    raw = await services.memory.retrieve(
                        query, top_k=limit_per_source,
                    )
                    if raw:
                        for block in raw.split("\n\n")[:limit_per_source]:
                            if block.strip():
                                hits.append({
                                    "source": "vault",
                                    "ref": "vault_rag",
                                    "snippet": block.strip()[:240],
                                    "ts": None,
                                })
            except Exception:
                pass

        # Newest-first sort with None timestamps last
        hits.sort(key=lambda h: (h.get("ts") or ""), reverse=True)

        # Group counts for the agent's eye
        from collections import Counter
        by_source = dict(Counter(h["source"] for h in hits))
        return {
            "ok": True,
            "query": query,
            "count": len(hits),
            "by_source": by_source,
            "hits": hits,
        }


tool = SmartSearchTool()
