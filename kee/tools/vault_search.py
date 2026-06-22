"""Tool: vault_search — substring search across the whole vault.

`memory_search` queries ChromaDB (semantic, requires the Auctorum worker).
`projects` only covers `vault/projects/`. `recall` is over conversation
messages. This tool fills the gap: literal substring across every `.md`
file in the vault, with snippets.

Useful when the user mentions a specific phrase you wrote down ("did I
ever note the Stripe webhook URL?") and you want the exact match without
embeddings or a remote round-trip.

Risk: 0 — local read.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_DEFAULT_SUBDIRS = (
    "notes", "_kee", "config", "projects", "people", "knowledge",
    "imports", "logs",
)


def _walk_md(roots: list[str]) -> list[Path]:
    base = settings.vault_dir
    files: list[Path] = []
    for sub in roots:
        d = base / sub
        if not d.exists():
            continue
        for p in d.rglob("*.md"):
            if p.is_file():
                files.append(p)
    return files


def _snippet(text: str, q: str, width: int = 200) -> str:
    idx = text.lower().find(q.lower())
    if idx == -1:
        return text[:width]
    half = width // 2
    start = max(0, idx - half)
    end = min(len(text), idx + len(q) + half)
    out = text[start:end].replace("\n", " ").strip()
    if start > 0:
        out = "…" + out
    if end < len(text):
        out = out + "…"
    return out


class VaultSearchTool(Tool):
    name = "vault_search"
    description = (
        "Substring search literal sobre todo el vault (.md files). "
        "Diferente de:\n"
        "  - `memory_search` (semantic, requiere ChromaDB)\n"
        "  - `projects search` (sólo vault/projects/)\n"
        "  - `notes search` (sólo vault/notes/)\n"
        "  - `recall` (sólo messages table)\n"
        "Úsalo cuando el usuario menciona una frase exacta que pudo "
        "haber quedado en alguna nota o en un import de chat. Devuelve "
        "snippets de ~200 chars centrados en el match.\n"
        "Roots por default: notes, _kee, config, projects, people, "
        "knowledge, imports, logs."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "roots": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subdirs del vault a buscar. Override del "
                               "default si quieres barrido amplio.",
            },
            "limit": {"type": "integer", "default": 10,
                      "description": "Cap de hits."},
        },
        "required": ["query"],
    }

    async def execute(
        self,
        query: str,
        roots: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if not query or not query.strip():
            return {"ok": False, "error": "query required"}
        scan_roots = roots or list(_DEFAULT_SUBDIRS)
        files = _walk_md(scan_roots)
        q_lower = query.lower()
        hits: list[dict[str, Any]] = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if q_lower not in text.lower():
                continue
            rel = p.relative_to(settings.vault_dir)
            hits.append({
                "path": str(rel).replace("\\", "/"),
                "abs_path": str(p),
                "snippet": _snippet(text, query),
                "size": len(text),
            })
            if len(hits) >= int(limit):
                break
        return {
            "ok": True,
            "query": query,
            "scanned_files": len(files),
            "scanned_roots": scan_roots,
            "count": len(hits),
            "hits": hits,
        }


tool = VaultSearchTool()
