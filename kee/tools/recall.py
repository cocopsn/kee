"""Tool: recall — search Kee's own past conversations.

`memory_search` queries the Obsidian vault (RAG over notes); this tool
queries the `messages` table — the literal turn-by-turn transcript Kee
has been accumulating since day one. Two retrieval modes:

  - **substring** (default): SQLite LIKE over `content`. Always works,
    zero deps.
  - **semantic**: if the ChromaDB worker is reachable, falls back to it
    via `services.memory` for fuzzier matches.

Use cases:
  - "¿qué le dije a Kee sobre AUCTORUM hace 3 semanas?"
  - "encuentra esa decisión de despliegue de la landing"
  - "rebuscar contexto antes de retomar un proyecto frío"

Returns: list of `{conversation_id, role, snippet, ts, score?}` ordered by
recency (substring) or similarity (semantic).

Risk: 0 — read-only over our own DB.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kee.core import db
from kee.tools.base import Tool


def _snippet(content: str, query: str, width: int = 160) -> str:
    """Return ~`width` chars centered on the first occurrence of `query`,
    falling back to the head of the message if no hit (e.g. semantic mode
    where the match isn't a literal substring)."""
    if not content:
        return ""
    text = content.replace("\n", " ").replace("\r", " ")
    if not query:
        return text[:width].strip()
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text[:width].strip() + ("…" if len(text) > width else "")
    half = max(40, width // 2)
    start = max(0, idx - half)
    end = min(len(text), idx + len(query) + half)
    out = text[start:end].strip()
    if start > 0:
        out = "…" + out
    if end < len(text):
        out = out + "…"
    return out


def _substring_search(
    query: str,
    *,
    role: str | None = None,
    conversation_id: str | None = None,
    days: int | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    if not query:
        return []
    con = db.get_connection()
    where = ["content LIKE ?"]
    params: list[Any] = [f"%{query}%"]
    if role:
        where.append("role = ?")
        params.append(role)
    if conversation_id:
        where.append("conversation_id = ?")
        params.append(conversation_id)
    if days is not None and days > 0:
        where.append("created_at >= datetime('now', ? || ' days')")
        params.append(f"-{int(days)}")
    sql = (
        "SELECT id, conversation_id, role, content, created_at FROM messages "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY id DESC LIMIT ?"
    )
    params.append(int(top_k))
    rows = con.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        ts = r[4]
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        out.append({
            "id": r[0],
            "conversation_id": r[1],
            "role": r[2],
            "snippet": _snippet(r[3] or "", query),
            "ts": ts,
        })
    return out


async def _semantic_fallback(query: str, top_k: int) -> list[dict[str, Any]]:
    """Ask `services.memory` (vault RAG) for semantic neighbours. Returns a
    flat list shaped like substring rows so the caller can merge."""
    try:
        from kee.core import services
        if services.memory is None:
            return []
        result = await services.memory.retrieve(query, top_k=top_k)
    except Exception:
        return []
    if not result:
        return []
    # `services.memory.retrieve` returns a string blob (joined passages).
    # Slice into pseudo-rows so the agent gets *something* structured.
    blocks = [b.strip() for b in result.split("\n\n") if b.strip()]
    return [
        {
            "id": None,
            "conversation_id": None,
            "role": "vault",
            "snippet": b[:240] + ("…" if len(b) > 240 else ""),
            "ts": None,
            "source": "vault_rag",
        }
        for b in blocks[:top_k]
    ]


class RecallTool(Tool):
    name = "recall"
    description = (
        "Search Kee's own past conversations (the messages table) for prior "
        "context — what was said, by whom, when. Use BEFORE asking the user "
        "to repeat himself, BEFORE reaching for memory_search (which queries "
        "the vault, not chat history), and to ground long-running threads "
        "('what did we agree about AUCTORUM 3 weeks ago?'). Substring match "
        "by default; opt-in semantic fallback via the vault RAG when the "
        "literal text isn't there.\n"
        "Returns: top-K matching messages with conversation_id, role, "
        "snippet, ts."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Substring or topic to look for.",
            },
            "role": {
                "type": "string",
                "enum": ["user", "assistant", "tool", "system"],
                "description": "Restrict to one role. Omit for all.",
            },
            "conversation_id": {
                "type": "string",
                "description": "Restrict to a single conversation.",
            },
            "days": {
                "type": "integer",
                "description": "Window in days (e.g. 30 for last month). "
                               "Omit for all-time.",
            },
            "top_k": {"type": "integer", "default": 5},
            "include_semantic": {
                "type": "boolean",
                "default": False,
                "description": "Also include vault RAG semantic matches "
                               "when no substring hit.",
            },
        },
        "required": ["query"],
    }

    async def execute(
        self,
        query: str,
        role: str | None = None,
        conversation_id: str | None = None,
        days: int | None = None,
        top_k: int = 5,
        include_semantic: bool = False,
    ) -> dict[str, Any]:
        substring = _substring_search(
            query,
            role=role,
            conversation_id=conversation_id,
            days=days,
            top_k=top_k,
        )
        out: dict[str, Any] = {
            "query": query,
            "matches": substring,
            "count": len(substring),
            "mode": "substring",
        }
        if include_semantic and not substring:
            sem = await _semantic_fallback(query, top_k=top_k)
            if sem:
                out["matches"] = sem
                out["count"] = len(sem)
                out["mode"] = "semantic_fallback"
        return out


tool = RecallTool()
