"""Tool: notes — read/search/create notes in the Obsidian vault.

Surfaces the existing `vault/` as a first-class tool the agent can use.
Notes default to `vault/notes/` (created on first write). Filenames are
slug-safe + timestamped to avoid collisions.

Risk:
  - read/search/list: 0
  - create/append: 1 (writes a file)
  - delete: 2 (irreversible without git)
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from kee.config import settings
from kee.tools.base import Tool


NOTES_DIR_NAME = "notes"
DEFAULT_GLOB = "**/*.md"


def notes_dir() -> Path:
    p = settings.vault_dir / NOTES_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:60] or "note"


def _list_notes(limit: int = 30) -> list[dict]:
    base = notes_dir()
    out = []
    for p in sorted(base.glob(DEFAULT_GLOB), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            text = p.read_text(encoding="utf-8")
            title = ""
            for line in text.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip(); break
            out.append({
                "path": str(p.relative_to(settings.vault_dir)),
                "title": title or p.stem,
                "size": len(text),
                "mtime": int(p.stat().st_mtime),
                "preview": text[:160].replace("\n", " "),
            })
        except Exception:
            continue
    return out


def _read_note(rel_path: str) -> dict:
    p = settings.vault_dir / rel_path
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not found: {rel_path}"}
    try:
        return {
            "ok": True,
            "path": str(p.relative_to(settings.vault_dir)),
            "content": p.read_text(encoding="utf-8"),
            "size": p.stat().st_size,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _search_notes(query: str, limit: int = 10) -> list[dict]:
    """Simple substring search across all .md in the vault.

    For semantic search, the agent should call `memory_search` (RAG via
    ChromaDB). This tool is the cheap-and-fast path."""
    q = query.lower()
    hits = []
    for p in settings.vault_dir.glob("**/*.md"):
        # Skip hidden / underscore folders (Obsidian internal, _kee/ archives)
        if any(part.startswith(".") for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if q not in text.lower():
            continue
        idx = text.lower().find(q)
        excerpt = text[max(0, idx - 80):idx + 200]
        hits.append({
            "path": str(p.relative_to(settings.vault_dir)),
            "title": p.stem,
            "match_idx": idx,
            "excerpt": excerpt,
        })
        if len(hits) >= limit:
            break
    return hits


def _create_note(title: str, content: str, folder: str = "") -> dict:
    base = notes_dir()
    if folder:
        base = base / _slugify(folder)
        base.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    name = f"{date}-{_slugify(title)}.md"
    p = base / name
    n = 0
    while p.exists():
        n += 1
        p = base / f"{date}-{_slugify(title)}-{n}.md"
    body = f"# {title}\n\n*{datetime.now().isoformat(timespec='minutes')}*\n\n{content}\n"
    p.write_text(body, encoding="utf-8")
    return {"ok": True, "path": str(p.relative_to(settings.vault_dir)),
            "size": p.stat().st_size}


def _append_note(rel_path: str, addition: str) -> dict:
    p = settings.vault_dir / rel_path
    if not p.exists():
        return {"ok": False, "error": f"not found: {rel_path}"}
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n\n---\n*{datetime.now().isoformat(timespec='minutes')}*\n\n{addition}\n")
        return {"ok": True, "path": rel_path, "appended_chars": len(addition)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class NotesTool(Tool):
    name = "notes"
    description = (
        "Read, search, list, create, or append notes in the Obsidian "
        "vault at vault/. Notes default to vault/notes/. For semantic "
        "search across the entire knowledge base use memory_search "
        "(ChromaDB RAG); this tool is the fast substring path.\n"
        "Actions:\n"
        "  - 'list':   recent notes (newest first)\n"
        "  - 'read':   full content of one note (path required)\n"
        "  - 'search': substring search across all .md\n"
        "  - 'create': new note (title required, body optional)\n"
        "  - 'append': append text to existing note"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "search", "create", "append"],
                "default": "list",
            },
            "query": {"type": "string"},
            "path": {"type": "string", "description": "Relative to vault/ (e.g. 'notes/2026-05-04-idea.md')"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "folder": {"type": "string", "description": "Subfolder under notes/ for organization"},
            "limit": {"type": "integer", "default": 30},
        },
    }

    async def execute(
        self,
        action: str = "list",
        query: str = "",
        path: str = "",
        title: str = "",
        content: str = "",
        folder: str = "",
        limit: int = 30,
    ) -> dict[str, Any]:
        if action == "list":
            return {"ok": True, "notes": _list_notes(limit=limit)}
        if action == "read":
            if not path:
                return {"ok": False, "error": "path required"}
            return _read_note(path)
        if action == "search":
            if not query:
                return {"ok": False, "error": "query required"}
            return {"ok": True, "query": query,
                    "matches": _search_notes(query, limit=limit)}
        if action == "create":
            if not title:
                return {"ok": False, "error": "title required"}
            return _create_note(title, content, folder)
        if action == "append":
            if not path or not content:
                return {"ok": False, "error": "path + content required"}
            return _append_note(path, content)
        return {"ok": False, "error": f"unknown action '{action}'"}


tool = NotesTool()
