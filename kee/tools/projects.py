"""Tool: projects — read + annotate Coco's project notes in vault/projects/.

The vault has scraped project READMEs + metadata under
`vault/projects/<slug>.md` (one per repo found by scripts/import_project_docs).
This tool gives the agent first-class access:

  - list: enumerate available project notes
  - get:  return full markdown of a specific project
  - search: substring across titles + bodies (cheap, no embedding)
  - append: add a dated note section to a project's file
  - stats: quick numerical overview (line count, last commit ts, has README)

Risk: 1 (read mostly; `append` writes to vault).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _root() -> Path:
    return settings.vault_dir / "projects"


def _slug_to_path(slug: str) -> Path:
    """Resolve a slug to its `.md` file. Accepts slug with or without .md."""
    s = slug.strip()
    if not s.endswith(".md"):
        s = s + ".md"
    return _root() / s


def _list_projects() -> list[dict[str, Any]]:
    root = _root()
    if not root.exists():
        return []
    out = []
    for p in sorted(root.glob("*.md")):
        try:
            st = p.stat()
            out.append({
                "slug": p.stem,
                "path": str(p),
                "bytes": st.st_size,
                "modified": datetime.utcfromtimestamp(st.st_mtime)
                                    .isoformat() + "Z",
            })
        except Exception:
            continue
    return out


def _search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    if not query:
        return []
    q = query.lower()
    hits: list[dict[str, Any]] = []
    for p in _root().glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        idx = text.lower().find(q)
        if idx == -1:
            continue
        # Snippet centred on hit
        start = max(0, idx - 80)
        end = min(len(text), idx + len(query) + 160)
        snippet = text[start:end].replace("\n", " ").strip()
        hits.append({
            "slug": p.stem,
            "path": str(p),
            "snippet": ("…" if start > 0 else "") + snippet
                       + ("…" if end < len(text) else ""),
        })
    return hits[:limit]


class ProjectsTool(Tool):
    name = "projects"
    description = (
        "Lee y anota los notes de proyectos en `vault/projects/<slug>.md` "
        "(generados por `scripts/import_project_docs.py`). Úsalo cuando "
        "Coco pregunta '¿qué tiene auctorum-systems?' o cuando quieres "
        "dejar una nota fechada en un proyecto.\n"
        "Acciones:\n"
        "  - 'list':   todos los proyectos con bytes + last modified\n"
        "  - 'get':    full markdown de un slug\n"
        "  - 'search': substring en titles + bodies (top 5)\n"
        "  - 'append': agrega un block fechado al final del .md\n"
        "  - 'stats':  meta rápida de un slug"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "search", "append", "stats"],
                "default": "list",
            },
            "slug": {"type": "string",
                     "description": "Project slug (filename sin .md)."},
            "query": {"type": "string"},
            "note": {"type": "string",
                     "description": "Texto a agregar (append only)."},
            "limit": {"type": "integer", "default": 5},
        },
    }

    async def execute(
        self,
        action: str = "list",
        slug: str | None = None,
        query: str | None = None,
        note: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        if action == "list":
            return {"ok": True, "projects": _list_projects()}

        if action == "search":
            if not query:
                return {"ok": False, "error": "query required"}
            hits = _search(query, limit=limit)
            return {"ok": True, "count": len(hits), "hits": hits}

        if action == "get":
            if not slug:
                return {"ok": False, "error": "slug required"}
            p = _slug_to_path(slug)
            if not p.exists():
                return {"ok": False, "error": f"no project '{slug}'"}
            try:
                body = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return {"ok": False, "error": str(e)}
            return {"ok": True, "slug": p.stem, "path": str(p),
                    "bytes": len(body), "body": body}

        if action == "stats":
            if not slug:
                return {"ok": False, "error": "slug required"}
            p = _slug_to_path(slug)
            if not p.exists():
                return {"ok": False, "error": f"no project '{slug}'"}
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return {"ok": False, "error": str(e)}
            return {
                "ok": True, "slug": p.stem,
                "bytes": len(text), "lines": text.count("\n") + 1,
                "headings": sum(1 for l in text.splitlines()
                                if l.startswith("#")),
                "has_readme_section": "## README" in text or "# README" in text,
            }

        if action == "append":
            if not slug or not note:
                return {"ok": False, "error": "slug + note required"}
            p = _slug_to_path(slug)
            if not p.exists():
                return {"ok": False, "error": f"no project '{slug}'"}
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            block = f"\n\n## Note {stamp}\n\n{note.strip()}\n"
            try:
                with p.open("a", encoding="utf-8") as fh:
                    fh.write(block)
            except Exception as e:
                return {"ok": False, "error": str(e)}
            return {"ok": True, "slug": p.stem, "appended_bytes": len(block)}

        return {"ok": False, "error": f"unknown action {action!r}"}


tool = ProjectsTool()
