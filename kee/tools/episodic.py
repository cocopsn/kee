"""Tool: episodic — semantic recall across EVERYTHING that happened.

Different from `recall` (substring over messages), `memory_search`
(vault notes), and `smart_search` (substring across many tables):
**episodic queries the unified ChromaDB index** that Sleep Cycle
Phase 13 maintains over conversations + dispatches + plans + focus
sessions + learnings + notifications + perception events.

Use cases:
  - "qué hicimos sobre auctorum stripe la semana pasada"
  - "cuál fue mi último focus session sobre kee"
  - "qué notificaciones urgentes me llegaron sobre seguros"

Returns ranked events with `{snippet, metadata: {kind, ref, ts, …},
similarity}`. Cite with `metadata.kind` + `metadata.ref` so Coco can
follow the trail back to the canonical row.

Risk: 0 — read-only over our own indexed history.
"""

from __future__ import annotations

from typing import Any

from kee.tools.base import Tool


class EpisodicTool(Tool):
    name = "episodic"
    description = (
        "Búsqueda semántica unificada sobre TODO lo que pasó: "
        "conversaciones, dispatches, planes, focus sessions, "
        "learnings, notificaciones y perception events. Diferente de "
        "`recall` (solo messages), `memory_search` (solo vault), "
        "`smart_search` (substring sobre tablas). Esto es el knowledge "
        "graph completo de Kee.\n"
        "Acciones:\n"
        "  - 'query' (default): semantic recall, opcional filtro `kinds`\n"
        "  - 'reindex': forzar re-index de la ventana (default 7d)\n"
        "Filtros válidos en `kinds`: conversation, dispatch, plan, "
        "focus, learning, notification, perception."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["query", "reindex"],
                "default": "query",
            },
            "query": {
                "type": "string",
                "description": "Pregunta o tema a buscar (query only).",
            },
            "kinds": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["conversation", "dispatch", "plan", "focus",
                             "learning", "notification", "perception"],
                },
                "description": "Restringir a uno o más tipos de evento.",
            },
            "n_results": {"type": "integer", "default": 5},
            "window_days": {
                "type": "integer", "default": 7,
                "description": "Ventana de re-indexación (reindex only).",
            },
        },
    }

    async def execute(
        self,
        action: str = "query",
        query: str | None = None,
        kinds: list[str] | None = None,
        n_results: int = 5,
        window_days: int = 7,
    ) -> dict[str, Any]:
        from kee.cognition.episodic_indexer import EpisodicIndexer
        idx = EpisodicIndexer()

        if action == "reindex":
            return await idx.index_window(window_days=int(window_days))

        if not query:
            return {"ok": False, "error": "query required"}
        return await idx.query(
            query=query, n_results=int(n_results), kinds=kinds,
        )


tool = EpisodicTool()
