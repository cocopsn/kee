"""Tool: learn — durable knowledge nuggets the agent explicitly remembers.

Different from:
  - `messages` (full transcript — high noise)
  - `vault/notes/` (long-form, human-curated)
  - `dispatch_registry` (project-level breadcrumbs)
  - `vault/_kee/learnings/` (Sleep Cycle-derived axioms)

`learn record topic="…" content="…"` lets the agent (or Coco) explicitly
pin a fact: "always use D:/Kee/node-globals for npm" or "Coco prefers
Sonnet for code review, not Haiku". Reinforced learnings rise to the top
of `recall`. Forgotten ones stay in the row (soft delete) for audit.

Risk: 1 — own table only.
"""

from __future__ import annotations

import logging
from typing import Any

from kee.core import db
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _row(row) -> dict[str, Any]:
    cols = ["id", "timestamp", "topic", "content", "source_msg_id",
            "reinforced", "forgotten", "forgotten_at"]
    d = {c: row[i] for i, c in enumerate(cols)}
    d["forgotten"] = bool(d["forgotten"])
    return d


class LearnTool(Tool):
    name = "learn"
    description = (
        "Pin a durable knowledge nugget Kee should remember across "
        "sessions. Use cuando Coco te corrige ('NO uses Haiku para code "
        "review') o cuando descubres un dato útil ('siempre `D:/Kee/"
        "node-globals` para npm'). NO uses para chit-chat ni para "
        "guardar el transcript completo (eso ya está en `messages`).\n"
        "Acciones:\n"
        "  - 'record':   pin nuevo (topic + content required)\n"
        "  - 'recall':   búsqueda por substring sobre topic+content\n"
        "  - 'reinforce': bump reinforced count (cuando se aplica de nuevo)\n"
        "  - 'forget':   soft-delete por id\n"
        "  - 'list':     últimos N activos\n"
        "  - 'top':      top reinforced activos"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["record", "recall", "reinforce", "forget",
                         "list", "top"],
                "default": "list",
            },
            "topic": {"type": "string"},
            "content": {"type": "string"},
            "query": {"type": "string"},
            "id": {"type": "integer"},
            "limit": {"type": "integer", "default": 10},
            "source_msg_id": {"type": "integer"},
        },
    }

    async def execute(
        self,
        action: str = "list",
        topic: str | None = None,
        content: str | None = None,
        query: str | None = None,
        id: int | None = None,
        limit: int = 10,
        source_msg_id: int | None = None,
    ) -> dict[str, Any]:
        if action == "record":
            if not topic or not content:
                return {"ok": False, "error": "topic + content required"}
            with db.cursor() as cur:
                cur.execute(
                    "INSERT INTO learnings "
                    "(topic, content, source_msg_id) VALUES (?, ?, ?)",
                    (topic.strip()[:120], content.strip()[:2000],
                     source_msg_id),
                )
                new_id = cur.lastrowid
            return {"ok": True, "id": new_id, "topic": topic}

        if action == "recall":
            if not query:
                return {"ok": False, "error": "query required"}
            con = db.get_connection()
            rows = con.execute(
                "SELECT id, timestamp, topic, content, source_msg_id, "
                "reinforced, forgotten, forgotten_at FROM learnings "
                "WHERE forgotten = 0 "
                "AND (topic LIKE ? OR content LIKE ?) "
                "ORDER BY reinforced DESC, id DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", int(limit)),
            ).fetchall()
            return {"ok": True, "count": len(rows),
                    "learnings": [_row(r) for r in rows]}

        if action == "reinforce":
            if id is None:
                return {"ok": False, "error": "id required"}
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE learnings SET reinforced = reinforced + 1 "
                    "WHERE id = ? AND forgotten = 0",
                    (int(id),),
                )
                if cur.rowcount == 0:
                    return {"ok": False,
                            "error": f"learning {id} not found or forgotten"}
            return {"ok": True, "id": int(id)}

        if action == "forget":
            if id is None:
                return {"ok": False, "error": "id required"}
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE learnings SET forgotten = 1, "
                    "forgotten_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND forgotten = 0",
                    (int(id),),
                )
                if cur.rowcount == 0:
                    return {"ok": False,
                            "error": f"learning {id} not found or already forgotten"}
            return {"ok": True, "forgot": int(id)}

        if action == "top":
            con = db.get_connection()
            rows = con.execute(
                "SELECT id, timestamp, topic, content, source_msg_id, "
                "reinforced, forgotten, forgotten_at FROM learnings "
                "WHERE forgotten = 0 ORDER BY reinforced DESC, id DESC "
                "LIMIT ?",
                (int(limit),),
            ).fetchall()
            return {"ok": True, "learnings": [_row(r) for r in rows]}

        # default: list
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, timestamp, topic, content, source_msg_id, "
            "reinforced, forgotten, forgotten_at FROM learnings "
            "WHERE forgotten = 0 ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return {"ok": True, "learnings": [_row(r) for r in rows]}


tool = LearnTool()
