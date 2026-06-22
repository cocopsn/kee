"""Memory tool — semantic search over the vault.

Delegates to the `MemoryManager` registered in `kee.core.services`. When the
ChromaDB worker is offline (Auctorum PC unreachable), the underlying call
returns an empty string and we report that gracefully to the model.

Risk: 0 (read-only).
"""

from __future__ import annotations

from typing import Any

from kee.core import services
from kee.tools.base import Tool


class MemorySearchTool(Tool):
    name = "memory_search"
    description = (
        "Semantic search the Obsidian vault for context relevant to a query. "
        "Use for: looking up project history, prior decisions, knowledge base "
        "entries, or anything Armando has written down. Returns top-K matching "
        "passages from the vault."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(self, query: str, top_k: int = 5) -> dict[str, Any]:
        if services.memory is None:
            return {"error": "Memory manager not initialized."}
        result = await services.memory.retrieve(query, top_k=top_k)
        if not result:
            return {
                "results": "",
                "note": (
                    "No semantic results — ChromaDB worker offline or vault "
                    "not yet indexed (Phase 3)."
                ),
            }
        return {"results": result}


tool = MemorySearchTool()
